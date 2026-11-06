"""Task 3: low-latency OpenAI-compatible streaming PII guardrail."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


logger = logging.getLogger("streaming-guardrail")
app = FastAPI(title="Streaming PII Guardrail", version="1.0.0")

LLM_STREAM_URL = os.getenv("LLM_STREAM_URL", "http://127.0.0.1:9002/v1/chat/completions")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
ROLLING_WINDOW_CHARS = int(os.getenv("ROLLING_WINDOW_CHARS", "128"))

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def luhn_valid(candidate: str) -> bool:
    digits = [int(char) for char in candidate if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def pii_spans(text: str) -> list[tuple[int, int]]:
    spans = [(match.start(), match.end()) for pattern in (EMAIL, SSN) for match in pattern.finditer(text)]
    spans.extend(
        (match.start(), match.end())
        for match in CARD_CANDIDATE.finditer(text)
        if luhn_valid(match.group())
    )
    if not spans:
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def redact_prefix(text: str, limit: int) -> tuple[str, int]:
    """Redact complete matches and return output plus raw characters consumed."""
    spans = pii_spans(text)
    # Never split a recognized PII match at the emission boundary.
    for start, end in spans:
        if start < limit < end:
            limit = start
            break
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        if end > limit:
            break
        pieces.append(text[cursor:start])
        pieces.append("[REDACTED]")
        cursor = end
    pieces.append(text[cursor:limit])
    return "".join(pieces), limit


class StreamingPIIRedactor:
    """A bounded rolling buffer that catches PII split across provider chunks."""

    def __init__(self, holdback: int = ROLLING_WINDOW_CHARS):
        if holdback < 32:
            raise ValueError("holdback must be at least 32 characters")
        self.holdback = holdback
        self.buffer = ""

    def feed(self, text: str) -> str:
        self.buffer += text
        emit_limit = max(0, len(self.buffer) - self.holdback)
        if emit_limit == 0:
            return ""
        output, consumed = redact_prefix(self.buffer, emit_limit)
        self.buffer = self.buffer[consumed:]
        return output

    def flush(self) -> str:
        output, consumed = redact_prefix(self.buffer, len(self.buffer))
        self.buffer = self.buffer[consumed:]
        return output


def content_path(event: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Locate an OpenAI chat delta content field."""
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    delta = choices[0].get("delta")
    if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
        return None
    return delta, delta["content"]


def sse(event: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"


async def guard_sse(source: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    redactor = StreamingPIIRedactor()
    byte_buffer = b""
    last_content_event: dict[str, Any] | None = None

    async for chunk in source:
        byte_buffer += chunk
        while b"\n" in byte_buffer:
            raw_line, byte_buffer = byte_buffer.split(b"\n", 1)
            line = raw_line.rstrip(b"\r")
            if not line:
                continue
            if not line.startswith(b"data:"):
                yield line + b"\n\n"
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                remaining = redactor.flush()
                if remaining and last_content_event is not None:
                    final_event = copy.deepcopy(last_content_event)
                    located = content_path(final_event)
                    assert located is not None
                    located[0]["content"] = remaining
                    yield sse(final_event)
                yield b"data: [DONE]\n\n"
                continue
            try:
                event = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Dropping malformed provider SSE event")
                continue
            if not isinstance(event, dict):
                continue
            located = content_path(event)
            if located is None:
                yield sse(event)
                continue
            delta, text = located
            last_content_event = copy.deepcopy(event)
            safe_text = redactor.feed(text)
            if safe_text:
                delta["content"] = safe_text
                yield sse(event)

    # Handle a provider that closes without a [DONE] sentinel.
    remaining = redactor.flush()
    if remaining and last_content_event is not None:
        final_event = copy.deepcopy(last_content_event)
        located = content_path(final_event)
        assert located is not None
        located[0]["content"] = remaining
        yield sse(final_event)


@app.post("/v1/chat/completions")
async def completions(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": {"code": "invalid_json", "message": "Invalid JSON body"}})
    if not isinstance(body, dict) or body.get("stream") is not True:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "stream_required", "message": "Set stream=true for this endpoint"}},
        )

    headers = {"content-type": "application/json"}
    if LLM_API_KEY:
        headers["authorization"] = f"Bearer {LLM_API_KEY}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
    try:
        upstream_request = client.build_request("POST", LLM_STREAM_URL, json=body, headers=headers)
        upstream = await client.send(upstream_request, stream=True)
    except (httpx.TimeoutException, httpx.NetworkError):
        await client.aclose()
        logger.exception("LLM provider unavailable")
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "provider_unavailable", "message": "Model provider unavailable"}},
        )
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "provider_error", "message": "Model provider rejected the request"}},
        )

    async def output() -> AsyncIterator[bytes]:
        try:
            async for safe_chunk in guard_sse(upstream.aiter_bytes()):
                yield safe_chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(output(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

