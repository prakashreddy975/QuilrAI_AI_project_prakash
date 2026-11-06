# Task 3 — Streaming LLM PII Guardrail

An OpenAI-compatible streaming proxy that inspects model output in real time and
redacts sensitive values before they reach the client.

## Supported PII

| Type | Example | Detection |
| --- | --- | --- |
| Email | `alice@example.com` | Pattern with boundary checks |
| US SSN | `123-45-6789` | Exact dashed format |
| Payment card | `4111 1111 1111 1111` | 13–19 digits plus valid Luhn checksum |

Matches are replaced with `[REDACTED]`.

## Streaming design

```text
Client request
    ▼
POST /v1/chat/completions
    ▼
Configured LLM provider
    │ OpenAI-compatible SSE
    ▼
Incremental SSE parser
    │ choices[0].delta.content
    ▼
Bounded rolling PII buffer
    ▼
Sanitized SSE stream
```

PII may be divided across provider chunks. Releasing each chunk immediately
could leak the first half of a value before the remainder arrives. The proxy
therefore retains a configurable suffix—128 characters by default—and emits
older text as soon as it is safe.

This provides bounded memory, cross-chunk matching, and continued streaming
without accumulating a complete completion.

## Files

```text
task3_streaming_guardrail/
├── README.md
├── app.py
├── requirements.txt
└── tests/
    └── test_redactor.py
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
| `LLM_STREAM_URL` | `http://127.0.0.1:9002/v1/chat/completions` | Provider endpoint |
| `LLM_API_KEY` | Empty | Optional provider bearer token |
| `ROLLING_WINDOW_CHARS` | `128` | Trailing characters retained; minimum `32` |

```bash
export LLM_STREAM_URL=https://provider.example/v1/chat/completions
export LLM_API_KEY=replace-with-provider-key
export ROLLING_WINDOW_CHARS=128
```

## Run

```bash
uvicorn app:app --host 127.0.0.1 --port 8001
curl http://127.0.0.1:8001/health
```

## Example request

```bash
curl --no-buffer http://127.0.0.1:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "provider-model-name",
    "stream": true,
    "messages": [
      {"role": "user", "content": "Repeat: alice@example.com"}
    ]
  }'
```

The endpoint requires `stream: true`. Non-streaming requests return a
`stream_required` error because this task evaluates streaming guardrails.

## Provider stream format

```text
data: {"choices":[{"delta":{"content":"Contact alice@"}}]}

data: {"choices":[{"delta":{"content":"example.com"}}]}

data: [DONE]
```

The client receives combined content containing `Contact [REDACTED]`. Events
without text are passed through. Malformed events are dropped and logged. The
remaining rolling suffix is sanitized before `[DONE]` is emitted.

## Error format

```json
{
  "error": {
    "code": "provider_unavailable",
    "message": "Model provider unavailable"
  }
}
```

Provider bodies, credentials, exception text, and stack traces are not exposed.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests cover split emails and SSNs, valid-card redaction, invalid-card
preservation, and the Luhn implementation.

## Limitations and extensions

- Redaction currently targets `choices[0].delta.content`.
- Regex guardrails are not a complete data-loss-prevention platform.
- Identifiers longer than the rolling window need a larger window or a
  state-machine detector.
- Production extensions include Unicode-aware patterns, custom identifiers,
  cancellation propagation, metrics, and configurable redact/block policies.

