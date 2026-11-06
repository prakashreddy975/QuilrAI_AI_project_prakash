"""Task 2: HTTP/JSON-RPC MCP authorization gateway."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response


logger = logging.getLogger("mcp-security-gateway")
app = FastAPI(title="MCP Security Gateway", version="1.0.0")

DOWNSTREAM_URL = os.getenv("DOWNSTREAM_MCP_URL", "http://127.0.0.1:9001/mcp")
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "10"))


@dataclass(frozen=True)
class Principal:
    token: str
    role: str


class TokenAuthenticator:
    """Constant-time concerns belong to the external identity provider.

    This assessment gateway maps opaque tokens to roles from an environment
    variable. In production the same interface can verify a JWT/JWKS token.
    """

    def __init__(self, token_roles: dict[str, str]):
        self._token_roles = token_roles

    @classmethod
    def from_environment(cls) -> "TokenAuthenticator":
        raw = os.getenv(
            "GATEWAY_TOKENS_JSON",
            '{"demo-admin-token":"admin","demo-viewer-token":"viewer"}',
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and v in {"admin", "viewer"} for k, v in parsed.items()
        ):
            raise RuntimeError("GATEWAY_TOKENS_JSON must map tokens to admin/viewer roles")
        return cls(parsed)

    def authenticate(self, authorization: str | None) -> Principal | None:
        if not authorization:
            return None
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token:
            return None
        role = self._token_roles.get(token)
        return Principal(token=token, role=role) if role else None


authenticator = TokenAuthenticator.from_environment()


def rpc_error(request_id: object, code: int, message: str, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
    )


def parse_rpc(payload: object) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    if not isinstance(payload, dict):
        return None, rpc_error(None, -32600, "Invalid Request")
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        return None, rpc_error(request_id, -32600, "Invalid Request")
    params = payload.get("params", {})
    if params is not None and not isinstance(params, dict):
        return None, rpc_error(request_id, -32602, "Invalid params")
    return payload, None


@app.post("/mcp")
async def proxy_mcp(request: Request, authorization: str | None = Header(default=None)) -> Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return rpc_error(None, -32700, "Parse error")

    rpc, error = parse_rpc(payload)
    if error:
        return error
    assert rpc is not None

    principal = authenticator.authenticate(authorization)
    if principal is None:
        return rpc_error(rpc.get("id"), -32000, "Authentication required", status=401)

    if rpc["method"] == "tools/call":
        params = rpc.get("params") or {}
        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return rpc_error(rpc.get("id"), -32602, "Invalid params")
        if tool_name.startswith("admin_") and principal.role != "admin":
            logger.warning("Blocked privileged tool=%s role=%s", tool_name, principal.role)
            return rpc_error(rpc.get("id"), -32001, "Unauthorized Tool Call")

    # tools/list and all authorized methods are forwarded without rewriting the
    # JSON-RPC body. The opaque bearer token is intentionally not sent onward.
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
            upstream = await client.post(
                DOWNSTREAM_URL,
                json=rpc,
                headers={"content-type": "application/json"},
            )
    except (httpx.TimeoutException, httpx.NetworkError):
        logger.exception("Downstream MCP server unavailable")
        return rpc_error(rpc.get("id"), -32002, "Downstream MCP server unavailable")

    content_type = upstream.headers.get("content-type", "application/json")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=content_type)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

