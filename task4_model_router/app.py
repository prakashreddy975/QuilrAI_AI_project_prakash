"""Task 4: SQLite token limiter and resilient LLM model router."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse


logger = logging.getLogger("model-router")
app = FastAPI(title="Resilient Model Router", version="1.0.0")

TOKEN_LIMIT = int(os.getenv("TOKEN_LIMIT_PER_MINUTE", "50000"))
WINDOW_SECONDS = int(os.getenv("TOKEN_WINDOW_SECONDS", "60"))
PRIMARY_URL = os.getenv("PRIMARY_MODEL_URL", "http://127.0.0.1:9003/v1/chat/completions")
SECONDARY_URL = os.getenv("SECONDARY_MODEL_URL", "http://127.0.0.1:9004/v1/chat/completions")
PRIMARY_TIMEOUT_SECONDS = float(os.getenv("PRIMARY_TIMEOUT_SECONDS", "3.0"))
DATABASE_PATH = Path(os.getenv("RATE_LIMIT_DB", str(Path(__file__).with_name("router.db"))))


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Tenant token rate limit exceeded")


class GatewayFailure(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class SQLiteTokenRateLimiter:
    """Exact sliding-window limiter with atomic SQLite reservations."""

    def __init__(self, path: Path, limit: int = 50_000, window_seconds: int = 60):
        self.path = path
        self.limit = limit
        self.window_ms = window_seconds * 1000
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path, timeout=10, isolation_level=None) as connection:
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS token_usage (
                           reservation_id TEXT PRIMARY KEY,
                           tenant_key TEXT NOT NULL,
                           created_at_ms INTEGER NOT NULL,
                           tokens INTEGER NOT NULL CHECK(tokens >= 0)
                       )"""
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_time "
                    "ON token_usage(tenant_key, created_at_ms)"
                )
            self._schema_ready = True

    def _connect(self) -> sqlite3.Connection:
        self._ensure_schema()
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _reserve(self, tenant: str, tokens: int, now_ms: int) -> str:
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self.limit:
            raise RateLimitExceeded(max(1, math.ceil(self.window_ms / 1000)))
        reservation_id = uuid.uuid4().hex
        cutoff = now_ms - self.window_ms
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM token_usage WHERE created_at_ms <= ?", (cutoff,))
            row = connection.execute(
                "SELECT COALESCE(SUM(tokens), 0), MIN(created_at_ms) "
                "FROM token_usage WHERE tenant_key = ? AND created_at_ms > ?",
                (tenant, cutoff),
            ).fetchone()
            used, oldest = int(row[0]), row[1]
            if used + tokens > self.limit:
                retry_ms = max(1, int(oldest) + self.window_ms - now_ms) if oldest else self.window_ms
                connection.rollback()
                raise RateLimitExceeded(max(1, math.ceil(retry_ms / 1000)))
            connection.execute(
                "INSERT INTO token_usage(reservation_id, tenant_key, created_at_ms, tokens) "
                "VALUES (?, ?, ?, ?)",
                (reservation_id, tenant, now_ms, tokens),
            )
            connection.commit()
        return reservation_id

    async def reserve(self, tenant: str, tokens: int, now_ms: int | None = None) -> str:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        return await asyncio.to_thread(self._reserve, tenant, tokens, timestamp)

    def _change_reservation(self, reservation_id: str, tokens: int | None) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if tokens is None:
                connection.execute("DELETE FROM token_usage WHERE reservation_id = ?", (reservation_id,))
            else:
                connection.execute(
                    "UPDATE token_usage SET tokens = ? WHERE reservation_id = ?",
                    (max(0, tokens), reservation_id),
                )
            connection.commit()

    async def reconcile(self, reservation_id: str, actual_tokens: int) -> None:
        await asyncio.to_thread(self._change_reservation, reservation_id, actual_tokens)

    async def release(self, reservation_id: str) -> None:
        await asyncio.to_thread(self._change_reservation, reservation_id, None)

    def _usage(self, tenant: str, now_ms: int) -> int:
        cutoff = now_ms - self.window_ms
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM token_usage "
                "WHERE tenant_key = ? AND created_at_ms > ?",
                (tenant, cutoff),
            ).fetchone()
        return int(row[0])

    async def usage(self, tenant: str, now_ms: int | None = None) -> int:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        return await asyncio.to_thread(self._usage, tenant, timestamp)


@dataclass(frozen=True)
class RoutedResponse:
    payload: dict[str, Any]
    provider: str


class ModelRouter:
    def __init__(
        self,
        primary_url: str,
        secondary_url: str,
        primary_timeout: float = 3.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.primary_url = primary_url
        self.secondary_url = secondary_url
        self.primary_timeout = primary_timeout
        self.client = client

    async def _post(self, client: httpx.AsyncClient, url: str, body: dict[str, Any], timeout: float) -> httpx.Response:
        return await client.post(url, json=body, timeout=timeout)

    @staticmethod
    def _validated_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GatewayFailure("invalid_provider_response", "Model provider returned an invalid response") from None
        if not isinstance(payload, dict):
            raise GatewayFailure("invalid_provider_response", "Model provider returned an invalid response")
        return payload

    async def complete(self, body: dict[str, Any]) -> RoutedResponse:
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient()
        fallback_reason: str | None = None
        try:
            try:
                # httpx applies phase-specific timeouts. asyncio.timeout adds a
                # hard wall-clock deadline so slow multi-phase responses cannot
                # exceed the required three-second primary budget.
                async with asyncio.timeout(self.primary_timeout):
                    primary = await self._post(client, self.primary_url, body, self.primary_timeout)
                if primary.status_code == 429:
                    fallback_reason = "primary_rate_limited"
                elif primary.is_success:
                    return RoutedResponse(self._validated_payload(primary), "primary")
                else:
                    raise GatewayFailure("primary_provider_error", "Primary model provider rejected the request")
            except (TimeoutError, httpx.TimeoutException):
                fallback_reason = "primary_timeout"
            except httpx.NetworkError:
                raise GatewayFailure("primary_unavailable", "Primary model provider is unavailable") from None

            logger.warning("Using secondary model: %s", fallback_reason)
            try:
                secondary = await self._post(client, self.secondary_url, body, 10.0)
            except (httpx.TimeoutException, httpx.NetworkError):
                raise GatewayFailure("fallback_unavailable", "Backup model provider is unavailable") from None
            if not secondary.is_success:
                raise GatewayFailure("fallback_provider_error", "Backup model provider rejected the request")
            return RoutedResponse(self._validated_payload(secondary), "secondary")
        finally:
            if owned_client:
                await client.aclose()


def estimate_tokens(body: dict[str, Any]) -> int:
    """Conservative preflight estimate: UTF-8 text / 4 plus max output tokens."""
    max_tokens = body.get("max_tokens", body.get("max_completion_tokens", 0))
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0:
        raise ValueError("max_tokens must be a non-negative integer")
    prompt = body.get("messages", body.get("prompt", ""))
    prompt_chars = len(json.dumps(prompt, ensure_ascii=False, separators=(",", ":")))
    return max(1, math.ceil(prompt_chars / 4) + max_tokens)


def gateway_error(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    error.update(extra)
    return JSONResponse(status_code=status, content={"error": error})


limiter = SQLiteTokenRateLimiter(DATABASE_PATH, TOKEN_LIMIT, WINDOW_SECONDS)
router = ModelRouter(PRIMARY_URL, SECONDARY_URL, PRIMARY_TIMEOUT_SECONDS)


@app.post("/v1/chat/completions")
async def completions(request: Request, x_api_key: str | None = Header(default=None)):
    if not x_api_key:
        return gateway_error(401, "authentication_required", "A tenant API key is required")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return gateway_error(400, "invalid_json", "Invalid JSON body")
    if not isinstance(body, dict):
        return gateway_error(400, "invalid_request", "Request body must be an object")
    try:
        estimated_tokens = estimate_tokens(body)
    except ValueError as exc:
        return gateway_error(400, "invalid_request", str(exc))

    try:
        reservation_id = await limiter.reserve(x_api_key, estimated_tokens)
    except RateLimitExceeded as exc:
        return gateway_error(
            429,
            "token_rate_limit_exceeded",
            "Tenant token budget exceeded",
            retry_after_seconds=exc.retry_after_seconds,
        )

    try:
        routed = await router.complete(body)
    except GatewayFailure as exc:
        await limiter.release(reservation_id)
        logger.warning("Gateway request failed code=%s", exc.code)
        return gateway_error(exc.status_code, exc.code, exc.message)
    except Exception:
        await limiter.release(reservation_id)
        logger.exception("Unexpected model routing failure")
        return gateway_error(502, "gateway_error", "The model request could not be completed")

    usage = routed.payload.get("usage")
    actual_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(actual_tokens, int) and not isinstance(actual_tokens, bool) and actual_tokens >= 0:
        await limiter.reconcile(reservation_id, actual_tokens)

    response = dict(routed.payload)
    response["gateway"] = {"provider": routed.provider}
    return JSONResponse(content=response)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

