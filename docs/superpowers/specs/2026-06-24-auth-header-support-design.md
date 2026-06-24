# Per-Rule Auth Header Support

**Status:** Draft
**Date:** 2026-06-24
**Scope:** Add optional per-rule authentication to incoming requests in `request-catcher`.

## Goal

Let users declare that a particular mock endpoint requires an auth header. A request without a valid value gets a configurable failure response; a request with a valid value gets the normal mock body. This lets the catcher simulate an auth-protected API and exercise client code that has to send the right key/token.

## Non-Goals

- Real JWT signature, expiration, or claim validation. The mock matches the **literal header value string**.
- Per-key different responses (i.e. "JWT for Alice → response A, JWT for Bob → response B"). A single rule accepts any of N values and returns one response.
- Authenticating access to `request-catcher` itself (the management UI / `/__*` endpoints). Those stay open.
- Templating the request's auth value into the response body. Out of scope for this change.

## Design Overview

Each `ResponseRule` gains two new optional fields:

- `auth: { header: str, values: list[str] }` — what the request must carry
- `on_auth_failure: { status_code, headers, body }` — what to return if it doesn't

`match_rule()` is unchanged. After a rule matches, the middleware evaluates the auth header (if required) and either returns the rule's normal response or `on_auth_failure`. Every request is still captured, now with an `auth_status` field so the UI can show which ones were rejected.

Validation is strict: a malformed `auth` block in `responses.yaml` causes `load_config` to raise a `pydantic.ValidationError`. Hot-reload of a bad config returns `400` and keeps the previously-loaded rules in memory.

## Data Model

**`app/models.py`** — additions:

```python
class AuthConfig(BaseModel):
    header: str                              # e.g. "X-API-Key", "Authorization"
    values: list[str]                        # any one of these is accepted


class AuthFailureResponse(BaseModel):
    status_code: int = 401
    headers: dict[str, str] = Field(
        default_factory=lambda: {"Content-Type": "application/json"}
    )
    body: str = '{"error":"unauthorized"}'


class ResponseRule(BaseModel):
    path: Optional[str] = None
    path_pattern: Optional[str] = None
    method: Optional[str] = None
    status_code: int = 200
    headers: dict[str, str] = Field(
        default_factory=lambda: {"Content-Type": "application/json"}
    )
    body: str = "{}"
    delay_ms: int = 0
    auth: Optional[AuthConfig] = None
    on_auth_failure: Optional[AuthFailureResponse] = None

    @model_validator(mode="after")
    def _on_auth_failure_requires_auth(self):
        if self.on_auth_failure is not None and self.auth is None:
            raise ValueError("on_auth_failure requires auth to be set")
        return self


class StoredRequest(BaseModel):
    # ... existing fields ...
    auth_status: Optional[str] = None        # "ok" | "missing" | "invalid" | None
    auth_header: Optional[str] = None        # the header that was required, if any
    auth_values_count: Optional[int] = None  # count of valid values, if any
```

`AuthFailureResponse` deliberately has **no `delay_ms`** — failed-auth responses are immediate.

## YAML Schema

Two new optional top-level keys per rule. Both default to `None`. `auth` without `on_auth_failure` uses built-in defaults; `on_auth_failure` without `auth` is a config error.

### Minimum (defaults applied for failure)

```yaml
- path: /api/secret
  method: GET
  auth:
    header: X-API-Key
    values: ["alpha-key"]
  body: '{"secret":"data"}'
```

Missing/wrong header → automatic `401` with body `{"error":"unauthorized"}` and `Content-Type: application/json`.

### Fully specified

```yaml
- path: /api/admin
  method: POST
  auth:
    header: X-API-Key
    values: ["alpha-key", "beta-key"]
  on_auth_failure:
    status_code: 403
    headers:
      Content-Type: application/json
      WWW-Authenticate: ApiKey
    body: '{"error":"forbidden","hint":"use X-API-Key"}'
  status_code: 200
  body: '{"ok":true}'
```

### JWT-style

```yaml
- path: /api/me
  method: GET
  auth:
    header: Authorization
    values:
      - "Bearer eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoidV9lbXBfMDAxIn0.signature"
  status_code: 200
  body: '{"user_id":"u_emp_001"}'
```

The full header value is matched, including the `Bearer ` prefix.

### With `path_pattern` and `delay_ms`

```yaml
- path_pattern: /api/internal/.*
  method: GET
  auth:
    header: X-API-Key
    values: ["internal-key"]
  delay_ms: 1500
  status_code: 200
  body: '{"ok":true}'
```

`auth` is independent of `method`/`path`/`path_pattern`/`delay_ms` — any combination is allowed.

### Pydantic-enforced validation

| Field | Rule |
|---|---|
| `auth.header` | non-empty string |
| `auth.values` | non-empty list |
| `on_auth_failure.status_code` | int, 100–599 |
| `on_auth_failure` without `auth` | rejected by `model_validator` |

## Request Flow

`app/main.py:22` middleware, restructured to compute `auth_status` before storing:

```
1.  Skip if path starts with /__    (management endpoints, unchanged)
2.  body_bytes = await request.body()
    body_str   = body_bytes.decode("utf-8", errors="replace") if body_bytes else None
3.  rule         = match_rule(method, path)         # unchanged
4.  auth_status, auth_header, auth_values_count = evaluate_auth(rule, headers)   # new helper
5.  sr = StoredRequest(..., auth_status=auth_status, auth_header=auth_header, auth_values_count=auth_values_count)
6.  store_request(sr)
7.  Apply response:
      rule is None                            → call_next()   (catch-all)
      auth_status is None or "ok"             → respond with rule body (+ delay)
      auth_status is "missing" or "invalid"   → respond with rule.on_auth_failure (or defaults)
```

The only behavioral change vs. today: `store_request` is called **after** `match_rule` so the captured request carries the correct `auth_status`.

### `evaluate_auth(rule, request_headers) -> tuple[Optional[str], Optional[str], Optional[int]]`

Returns `(auth_status, auth_header, auth_values_count)`. The latter two are populated only when auth was actually checked (i.e., a rule matched **and** it had an `auth` block); otherwise both are `None`.

| Condition | `auth_status` | `auth_header` | `auth_values_count` |
|---|---|---|---|
| `rule is None` | `None` | `None` | `None` |
| `rule.auth is None` | `None` | `None` | `None` |
| header missing (case-insensitive name lookup) | `"missing"` | `rule.auth.header` | `len(rule.auth.values)` |
| header present, value not in `rule.auth.values` | `"invalid"` | `rule.auth.header` | `len(rule.auth.values)` |
| header present, value in `rule.auth.values` | `"ok"` | `rule.auth.header` | `len(rule.auth.values)` |

Header **name** comparison: case-insensitive (HTTP spec). Header **value** comparison: exact, case-sensitive.

Empty header value (`Authorization: ""`) is treated as `"invalid"` (header present, empty value), not `"missing"`.

Lives in `app/auth.py` (new file, ~30 lines). Models stay in `app/models.py` per the design decision.

## Error Handling

### `load_config` / startup

- Any `ValidationError` in a rule (including bad `auth` / `on_auth_failure`) propagates up.
- On server startup (`app/main.py:18`), the exception bubbles and the server crashes with a clear traceback. Acceptable for a dev tool — easier to notice than a half-loaded config.

### `POST /__config/reload`

- Bad config → `400 Bad Request` with `{"error": "<pydantic message>"}`.
- The **previously-loaded rules stay in memory** and continue serving. The reload call never wipes a working config.
- Implementation: try/except around `ResponseRule(**r)` construction; on error, return JSONResponse(400) and do not touch `_rules`.

### `/__config` (GET)

- Returns the rule list including `auth` and `on_auth_failure` exactly as loaded.
- Secrets in `auth.values` are visible. Acceptable because this is a local dev tool.

## API Changes

| Endpoint | Change |
|---|---|
| `GET /__requests` | Each entry now includes `auth_status`, `auth_header`, and `auth_values_count` (all nullable) |
| `GET /__config` | Each rule now includes `auth` and `on_auth_failure` (both nullable) |
| `POST /__config/reload` | May now return `400` with `{"error": "..."}` instead of always `200 {"ok": true}` |
| Everything else | Unchanged |

## Frontend Changes (`app/templates/index.html`)

Minimal, no new dependencies. Two small changes inside the existing single-file template:

- **Requests table — per-row auth indicator** (a new small column or inline span on each row):
  - green dot for `"ok"`
  - amber dot for `"missing"`
  - red dot for `"invalid"`
  - nothing for `null` (rule had no auth, or no rule matched)
- **Captured request detail row** (the togglable `<tr class="row-detail">` that already shows Headers / Body / Client) gets a new **Auth** section:
  - shows `auth_status` as a human-readable string
  - shows the expected header name + count of accepted values (e.g. `X-API-Key: 2 values`) when `auth_status` is anything other than `null`
  - omitted entirely when `auth_status` is `null` (no rule required auth)

The Config tab is a raw JSON dump in a `<pre>`; no per-rule UI changes are needed there.

## Testing

**`tests/test_config.py`** — extend with:

- Load a rule with `auth: { header, values }` → succeeds
- Load a rule with `auth` + `on_auth_failure` → succeeds
- `values: []` → `ValidationError`
- `header: ""` → `ValidationError`
- `on_auth_failure` declared without `auth` → `ValidationError`
- `on_auth_failure.status_code: 999` → `ValidationError`

**`tests/test_main.py`** — extend with:

- Valid `X-API-Key` → rule's normal body, `auth_status: "ok"`
- Missing header → `401` default body, `auth_status: "missing"`
- Wrong value → `401` default body, `auth_status: "invalid"`
- Multiple valid values, any one works → `auth_status: "ok"`
- Header name case-insensitive: `x-api-key: alpha` matches `X-API-Key`
- Value case-sensitive: `X-API-Key: ALPHA` does **not** match `values: ["alpha"]`
- Custom `on_auth_failure: { status_code: 403, ... }` → returns that 403
- Rule without `auth` → `auth_status: null`, normal response
- `path_pattern` rule with `auth` → both pattern match and auth required
- No rule matches → `auth_status: null`, catch-all response
- `POST /__config/reload` with bad auth rule → `400`; previously-loaded rules still serve

No new test framework, fixtures, or files — extend the existing `client` fixture and `load_config` patterns in `tests/conftest.py` and `test_main.py`. Estimated **~16 new test cases** (6 in `test_config.py`, 10 in `test_main.py`).

## Backward Compatibility

- Existing rules without `auth` / `on_auth_failure` continue to work unchanged.
- `StoredRequest` gains three new optional fields (`auth_status`, `auth_header`, `auth_values_count`). Old data (none persisted — in-memory only) and old API consumers get `null` for all three on entries that didn't have them set.
- `GET /__config` response gains two new keys per rule (`auth`, `on_auth_failure`), both nullable. UI consumers can ignore them.

## Files Touched

| File | Change |
|---|---|
| `app/models.py` | Add `AuthConfig`, `AuthFailureResponse`; extend `ResponseRule` and `StoredRequest`; add `model_validator` |
| `app/auth.py` | **New.** `evaluate_auth(rule, request_headers) -> tuple[Optional[str], Optional[str], Optional[int]]` |
| `app/main.py` | Restructure middleware to call `evaluate_auth` and set `auth_status`; wrap `reload_config_endpoint` with try/except for `ValidationError` |
| `app/templates/index.html` | Add auth-status dot per row in Requests table; add Auth section to captured request detail row |
| `tests/test_config.py` | Add validation tests (~6 cases) |
| `tests/test_main.py` | Add behavior tests (~10 cases) |
| `README.md` | Add a "Per-Rule Auth" subsection to "Response Rules" with YAML examples |
| `responses.yaml.example` | Add one example rule with `auth` |

## Open Questions

None. All design decisions resolved during brainstorming.
