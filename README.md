# Request Catcher

HTTP request capture and response mock server for testing and debugging.

## Quick Start

```bash
docker compose up
```

Open http://localhost:8443 — send requests, watch them appear in the table.

## How It Works

1. **All requests** to any path are captured and shown in the frontend (last 100, in-memory)
2. **Response rules** in `responses.yaml` let you configure exact replies — status code, headers, body, even simulated delay
3. **Unmatched requests** fall through to a catch-all handler that returns a JSON acknowledgment

## Response Rules

Edit `responses.yaml` — the server hot-reloads it via the frontend Config tab or `POST /__config/reload`.

```yaml
rules:
  - path: /api/hello
    method: GET
    status_code: 200
    headers:
      Content-Type: application/json
    body: '{"message": "hello"}'

  - path_pattern: /api/users/.*
    method: GET
    status_code: 200
    body: '{"users": [{"id": 1, "name": "Alice"}]}'

  - path: /api/slow
    delay_ms: 3000
    body: '{"done": "after 3s"}'
```

Matching priority: `method` filter first, then `path` (exact match), then `path_pattern` (regex). First match wins.

## Management Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Frontend UI |
| `GET /__requests` | List captured requests (JSON) |
| `GET /__requests/{id}` | Single request detail |
| `DELETE /__requests` | Clear all stored requests |
| `GET /__config` | Current response rules (JSON) |
| `POST /__config/reload` | Hot-reload `responses.yaml` |

## Run Locally

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

## Run Tests

```bash
uv sync --extra dev
uv run pytest -v
```
