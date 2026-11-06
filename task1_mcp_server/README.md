# Task 1 — Strict MCP Server

A runnable Model Context Protocol server that exposes customer lookup and refund
tools over stdio. It uses the official MCP Python SDK and Pydantic validation.

## Requirements addressed

- `get_customer_record` and `trigger_refund` MCP tools
- Customer IDs formatted as `CUST-XXXXX`
- Positive, finite refund amounts
- Refund reasons with at least 10 non-whitespace characters
- Strict Pydantic models with unknown fields forbidden
- JSON-RPC `-32602` errors for invalid tool arguments
- Stdio transport with logs isolated on stderr

## Files

```text
task1_mcp_server/
├── README.md
├── requirements.txt
├── server.py
└── tests/
    ├── test_protocol.py
    └── test_validation.py
```

## Tools

### `get_customer_record`

Looks up one in-memory customer record.

| Field | Type | Validation |
| --- | --- | --- |
| `customer_id` | string | Must match `^CUST-[A-Z0-9]{5}$` |

Example arguments:

```json
{
  "customer_id": "CUST-A1B2C"
}
```

Example result:

```json
{
  "found": true,
  "record": {
    "customer_id": "CUST-A1B2C",
    "name": "Avery Chen",
    "status": "active",
    "email": "avery@example.com"
  }
}
```

A syntactically valid ID without a matching record returns `found: false`. It
is a normal lookup result rather than a protocol failure.

### `trigger_refund`

Validates and accepts a refund request.

| Field | Type | Validation |
| --- | --- | --- |
| `customer_id` | string | Must match `^CUST-[A-Z0-9]{5}$` |
| `amount` | number | Greater than zero and finite |
| `reason` | string | 10–500 characters; at least 10 after trimming |

Example arguments:

```json
{
  "customer_id": "CUST-A1B2C",
  "amount": 49.95,
  "reason": "Duplicate subscription charge"
}
```

## Validation errors

Tool inputs are converted into strict Pydantic models inside the handler. A
failure raises an MCP protocol error with JSON-RPC code `-32602` and structured
field details:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32602,
    "message": "Invalid tool arguments",
    "data": [
      {
        "field": "customer_id",
        "message": "Value error, customer_id must match CUST-XXXXX",
        "type": "value_error"
      }
    ]
  }
}
```

## Install and run

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python server.py
```

The process waits for MCP messages on stdin. A host or MCP client normally
starts it as a subprocess; ordinary terminal text is not valid protocol input.

## Inspect interactively

```bash
mcp dev server.py
```

Example MCP host configuration:

```json
{
  "mcpServers": {
    "customer-operations": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/task1_mcp_server/server.py"]
    }
  }
}
```

Use absolute paths because desktop MCP hosts may start from another working
directory.

## Stdio isolation

The SDK owns stdout because a single stray line can corrupt the protocol. The
implementation therefore never calls `print()`, sends Python logs to stderr,
leaves JSON-RPC framing to the SDK, and starts only under the standard
`if __name__ == "__main__"` guard.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests verify validation edge cases, registered tool names, successful in-memory
MCP calls, and the exact `INVALID_PARAMS` error code.

## Extension points

The assessment uses an in-memory customer map and deterministic refund IDs.
Production integrations can replace those sections with database and payment
provider calls while preserving the public schemas and error behavior.

