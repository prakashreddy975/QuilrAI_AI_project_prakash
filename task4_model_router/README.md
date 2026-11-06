# Task 4 — Token Rate Limiter and Model Fallback Router

A resilient OpenAI-compatible completion gateway with a per-tenant token budget,
on-disk SQLite sliding window, hard primary-model deadline, and automatic
secondary-provider fallback.

## Request flow

```text
Client + X-API-Key
        ▼
Validate request and estimate tokens
        ▼
Atomically reserve tokens in SQLite
        ▼
Primary ── success ───────────────────► response
   ├── HTTP 429 ──► Secondary ─────────► response
   └── > 3000 ms ─► Secondary ─────────► response

Success: reconcile with usage.total_tokens
Failure: release reservation and sanitize error
```

## Requirements addressed

- 50,000 tokens per minute per tenant API key
- Persistent on-disk SQLite storage
- Atomic concurrent reservations
- Fallback on primary HTTP 429 or 3,000 ms timeout
- Standard errors without upstream details
- Reservation release on failure and reconciliation on success

## Files

```text
task4_model_router/
├── README.md
├── app.py
├── requirements.txt
└── tests/
    └── test_router.py
```

## Rate-limiting algorithm

The gateway makes a conservative preflight reservation:

```text
estimated tokens = ceil(serialized prompt characters / 4) + max_tokens
```

Within a SQLite `BEGIN IMMEDIATE` transaction, it deletes expired rows, sums
unexpired tenant usage, rejects an over-budget request, or inserts the new
reservation. The write lock prevents concurrent requests from both observing
the same unused capacity.

After success, `usage.total_tokens` replaces the estimate when available. If
routing fails completely, the reservation is removed.

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
| `PRIMARY_MODEL_URL` | `http://127.0.0.1:9003/v1/chat/completions` | Primary endpoint |
| `SECONDARY_MODEL_URL` | `http://127.0.0.1:9004/v1/chat/completions` | Backup endpoint |
| `PRIMARY_TIMEOUT_SECONDS` | `3.0` | Hard primary deadline |
| `TOKEN_LIMIT_PER_MINUTE` | `50000` | Tenant token limit |
| `TOKEN_WINDOW_SECONDS` | `60` | Sliding-window duration |
| `RATE_LIMIT_DB` | `router.db` beside `app.py` | SQLite path |

```bash
export PRIMARY_MODEL_URL=http://127.0.0.1:9003/v1/chat/completions
export SECONDARY_MODEL_URL=http://127.0.0.1:9004/v1/chat/completions
export PRIMARY_TIMEOUT_SECONDS=3.0
export TOKEN_LIMIT_PER_MINUTE=50000
export TOKEN_WINDOW_SECONDS=60
export RATE_LIMIT_DB=./router.db
```

## Run

```bash
uvicorn app:app --host 127.0.0.1 --port 8002
curl http://127.0.0.1:8002/health
```

## Example request

```bash
curl http://127.0.0.1:8002/v1/chat/completions \
  -H 'X-API-Key: tenant-a-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "preferred-model",
    "messages": [
      {"role": "user", "content": "Summarize the incident report."}
    ],
    "max_tokens": 500
  }'
```

Successful provider JSON is preserved and receives routing metadata:

```json
{
  "id": "completion-id",
  "choices": [],
  "usage": {"total_tokens": 237},
  "gateway": {"provider": "primary"}
}
```

`gateway.provider` is `secondary` when fallback occurs.

## Fallback behavior

| Primary outcome | Behavior |
| --- | --- |
| HTTP 2xx with a JSON object | Return primary response |
| HTTP 429 | Call secondary once |
| Hard deadline exceeded | Cancel primary and call secondary once |
| Network failure | Return `primary_unavailable` |
| Other non-2xx | Return `primary_provider_error` |

The secondary request has a 10-second timeout. Its failures are also sanitized.

## Rate-limit response

```json
{
  "error": {
    "code": "token_rate_limit_exceeded",
    "message": "Tenant token budget exceeded",
    "retry_after_seconds": 42
  }
}
```

This uses HTTP 429. Retry time is calculated from the oldest active reservation.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests cover expiration, tenant isolation, reservation release, atomic concurrent
requests, token estimation, primary success, HTTP 429 fallback, and wall-clock
timeout fallback.

## Operational notes

- SQLite uses WAL mode and a busy timeout.
- Blocking database work runs through `asyncio.to_thread`.
- `X-API-Key` identifies the tenant for this assessment; production systems
  should authenticate and hash API keys before this service.
- One SQLite file supports processes on one host. Multi-host enforcement needs
  a shared transactional store.
- Add provider authorization, circuit breaking, metrics, and traces before
  production use.

