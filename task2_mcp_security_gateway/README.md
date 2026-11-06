# Task 2 — MCP Security Gateway

A lightweight FastAPI reverse proxy that authenticates an MCP client, inspects
JSON-RPC requests, and blocks privileged tool calls before they reach a
downstream MCP server.

## Request flow

```text
AI agent / MCP client
        │ POST /mcp + Bearer token
        ▼
MCP Security Gateway
  1. Parse JSON
  2. Validate JSON-RPC envelope
  3. Resolve token role
  4. Enforce tool policy
        │ allowed requests only
        ▼
Downstream MCP HTTP endpoint
```

## Authorization policy

| Request | Viewer | Admin |
| --- | --- | --- |
| `tools/list` | Forwarded | Forwarded |
| Normal `tools/call` | Forwarded | Forwarded |
| `tools/call` with an `admin_` tool name | Blocked | Forwarded |

Blocked calls return JSON-RPC error `-32001` directly. The downstream server is
not contacted.

## Files

```text
task2_mcp_security_gateway/
├── README.md
├── app.py
├── requirements.txt
└── tests/
    └── test_gateway.py
```

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `DOWNSTREAM_MCP_URL` | `http://127.0.0.1:9001/mcp` | Downstream JSON-RPC endpoint |
| `GATEWAY_TOKENS_JSON` | Demo admin/viewer tokens | JSON token-to-role map |
| `UPSTREAM_TIMEOUT_SECONDS` | `10` | Downstream timeout |

```bash
export DOWNSTREAM_MCP_URL=http://127.0.0.1:9001/mcp
export GATEWAY_TOKENS_JSON='{"local-admin-key":"admin","local-viewer-key":"viewer"}'
export UPSTREAM_TIMEOUT_SECONDS=10
```

The default demo tokens are for local evaluation only.

## Run

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

## Examples

List tools as a viewer:

```bash
curl http://127.0.0.1:8000/mcp \
  -H 'Authorization: Bearer local-viewer-key' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Attempt an admin tool as a viewer:

```bash
curl http://127.0.0.1:8000/mcp \
  -H 'Authorization: Bearer local-viewer-key' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"admin_reset_key","arguments":{}}}'
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "error": {
    "code": -32001,
    "message": "Unauthorized Tool Call"
  }
}
```

## Error behavior

| Condition | JSON-RPC code | HTTP status |
| --- | ---: | ---: |
| Malformed JSON | `-32700` | `200` |
| Invalid JSON-RPC envelope | `-32600` | `200` |
| Invalid tool parameters | `-32602` | `200` |
| Missing or invalid token | `-32000` | `401` |
| Unauthorized `admin_` tool | `-32001` | `200` |
| Downstream unavailable | `-32002` | `200` |

The client's bearer token is not forwarded downstream, limiting credential
exposure at the gateway boundary.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests cover role extraction, invalid authentication schemes, JSON-RPC envelope
validation, and valid tool-call parsing.

## Production considerations

Replace the static map with JWT/JWKS validation or identity-provider token
introspection. Add key rotation, TLS, request-size limits, audit events, and a
policy engine if authorization becomes more complex than the prefix rule.

