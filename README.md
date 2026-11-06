# AI Field Developer / Solutions Engineer Assessment

Four independent Python implementations covering MCP servers, security
gateways, streaming guardrails, and resilient LLM routing. The solutions are
based on the requirements in `AI Field Developer Test.docx.pdf` and are kept in
separate directories so each task can be reviewed, installed, and run on its
own.

## Deliverables

| Task | Project | What it demonstrates |
| --- | --- | --- |
| 1 | [Strict MCP Server](task1_mcp_server/) | Official MCP SDK, stdio transport, strict Pydantic validation, JSON-RPC errors, and stdout isolation |
| 2 | [MCP Security Gateway](task2_mcp_security_gateway/) | Bearer authentication, role-based tool filtering, JSON-RPC parsing, and reverse proxying |
| 3 | [Streaming PII Guardrail](task3_streaming_guardrail/) | Incremental SSE processing, cross-chunk PII detection, bounded buffering, and low-latency redaction |
| 4 | [Model Router](task4_model_router/) | SQLite token limiting, timeout handling, model fallback, and sanitized errors |

## Repository layout

```text
.
├── AI Field Developer Test.docx.pdf
├── README.md
├── task1_mcp_server/
│   ├── server.py
│   ├── requirements.txt
│   └── tests/
├── task2_mcp_security_gateway/
│   ├── app.py
│   ├── requirements.txt
│   └── tests/
├── task3_streaming_guardrail/
│   ├── app.py
│   ├── requirements.txt
│   └── tests/
└── task4_model_router/
    ├── app.py
    ├── requirements.txt
    └── tests/
```

## Requirements

- Python 3.11 or newer
- `pip` and `venv`
- Network access when installing dependencies or connecting to upstream services

Runtime settings are supplied through environment variables. No provider
credentials or tenant secrets are committed to source control.

## Quick start

Each task is self-contained. A separate virtual environment for each task keeps
its dependencies isolated:

```bash
cd task1_mcp_server
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python server.py
```

Replace `task1_mcp_server` with the task you want to run and follow that task's
README for configuration and launch instructions.

## Run the test suites

After installing the relevant requirements, run from the repository root:

```bash
python -m unittest discover -s task1_mcp_server/tests -v
python -m unittest discover -s task2_mcp_security_gateway/tests -v
python -m unittest discover -s task3_streaming_guardrail/tests -v
python -m unittest discover -s task4_model_router/tests -v
```

The repository contains 24 tests covering:

- MCP tool registration, invocation, and `-32602` validation errors
- Bearer role extraction and JSON-RPC request validation
- Emails, SSNs, and card numbers divided across streaming chunks
- Luhn validation and false-positive preservation
- Sliding-window expiration, tenant isolation, reservation release, and atomic
  concurrent requests
- Primary success, HTTP 429 fallback, and wall-clock timeout fallback

## Cross-cutting design decisions

### Secure errors

Clients receive stable error codes and short public messages. Raw upstream
responses, Python exception messages, internal URLs, and stack traces stay out
of API responses.

### Concurrency

Network operations are asynchronous. The SQLite limiter moves blocking database
work to worker threads and wraps the usage check and reservation insert in a
single `BEGIN IMMEDIATE` transaction.

### Streaming memory use

The PII gateway never accumulates a complete model response. It retains a small
rolling suffix so patterns crossing provider chunks can still be recognized.

### Configuration

Provider URLs, timeouts, database paths, tokens, and limits are configured with
environment variables. This keeps the code portable across local development,
containers, and managed deployments.

## Example ports

| Component | Port |
| --- | ---: |
| MCP security gateway | `8000` |
| Streaming PII guardrail | `8001` |
| Model router | `8002` |
| Downstream/mock services | `9001`–`9004` |

These are example values and can be changed through launch arguments or
environment configuration.

## Production hardening

These are compact assessment implementations. Before production use, add
centralized identity verification, secret management, TLS, structured audit
logging, distributed tracing, metrics, and deployment-level health checks.
SQLite satisfies the requested single-node design; multi-node rate limiting
would require a shared transactional datastore.

