# Conditional Matching by Query String and Body

**Status:** Draft
**Date:** 2026-06-24
**Scope:** Add a per-rule `match` block to `ResponseRule` so a rule can match on top-level keys in the request's query string and/or JSON body, on top of the existing path/method match.

## Goal

Let users write multiple rules for the same path+method that return different responses based on a request parameter — e.g. one rule per `user_id` that returns a different JWT, or one rule per `platform_user_id` that returns a different identity record. The matcher extends the existing path/method match with a new dimension (parameter values), AND'd together.

This unlocks the headline use case: simulate an API where different `user_id` values get different responses, without templating.

## Non-Goals

- Templating the response body from request data. The response is static per rule; different inputs get different rules, not different rendered output.
- JSONPath, nested keys, or array element matching. Top-level keys only, scalar values only.
- Regex or operator-rich matching. Equality only.
- Form-encoded body parsing. JSON only.
- Reusing the auth feature's header matching. The auth feature is a separate, orthogonal dimension.

## Design Overview

Add one new optional field to `ResponseRule`:

```yaml
match: { key: value, ... }     # top-level key/value pairs from query or body
```

`match_rule()` becomes path/method/match-aware. A rule fires only when:

- `method` matches (if set)
- `path` or `path_pattern` matches
- **AND** every `(key, value)` in `match` is satisfied by the request's query string or JSON body

First matching rule in YAML order wins. Existing rules without `match` are unchanged.

## Data Model

**`app/models.py`** — one new field on `ResponseRule`:

```python
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
    auth: Optional[AuthConfig] = None                       # from auth feature
    on_auth_failure: Optional[AuthFailureResponse] = None  # from auth feature
    match: Optional[dict[str, str]] = None                  # NEW
```

`match` is strictly `dict[str, str]`. Pydantic rejects non-string values at load time (strict mode, same posture as the auth feature). Values are compared as strings.

**`StoredRequest` is unchanged.** The captured request does not need to expose which rule matched; the response is the proof.

## YAML Schema

`match` is an optional dict. Empty `match: {}` is allowed (no-op). `None`/missing values in YAML are rejected.

### Per-user JWT (headline use case)

```yaml
# /api/v1/auth/token — JWT for Alice
- path: /api/v1/auth/token
  method: POST
  match:
    user_id: u_emp_001
  status_code: 200
  body: '{"token":"eyJ...ALICE..."}'

# /api/v1/auth/token — JWT for Bob
- path: /api/v1/auth/token
  method: POST
  match:
    user_id: u_emp_002
  status_code: 200
  body: '{"token":"eyJ...BOB..."}'

# /api/v1/auth/token — unknown user → 404
- path: /api/v1/auth/token
  method: POST
  match:
    user_id: u_unknown
  status_code: 404
  body: '{"error":"user not found"}'
```

First match wins. Alice's request never sees Bob's rule.

### Query string match

```yaml
# /api/v1/identity/resolve?platform=line&platform_user_id=U736...
- path: /api/v1/identity/resolve
  method: GET
  match:
    platform: line
    platform_user_id: U73632230982d3bb5e099db5242a573e0
  status_code: 200
  body: '{"user_id":"U736...","type":"employee","role":"member"}'
```

Two match keys, both must hit (AND).

### Combined with auth (orthogonal dimensions)

```yaml
- path: /api/v1/identity/resolve
  method: GET
  match:
    platform: line
    platform_user_id: U736...
  auth:
    header: X-API-Key
    values: ["alpha-key"]
  body: '{"user_id":"U736..."}'
```

A request must match path+method+all `match` keys + carry a valid auth header. If `match` fails, the request falls through to the next rule without running the auth check for that rule.

### Fall-through: no `match` block

A rule without `match` matches purely on path/method, exactly as today. The two rule styles mix freely in the same file.

### Pydantic-enforced validation

| Field | Rule |
|---|---|
| `match` value (e.g. `count: 5`) | must be a string — quote it: `count: "5"` |
| `match: { user_id: ~ }` (YAML null) | rejected |
| `match: {}` | allowed, no-op |

## Match Logic

`app/config.py` signature change:

```python
def match_rule(
    method: str,
    path: str,
    query_params: dict[str, str],
    body_str: str | None,
) -> ResponseRule | None
```

The function iterates through rules in YAML order and returns the first one where:

1. `method` matches (or `method` is unset on the rule), AND
2. `path` matches exactly OR `path_pattern` matches as regex, AND
3. every `(key, expected_value)` in `rule.match` is satisfied by the request's data.

### Per-key lookup (body wins)

For each `(key, expected_value)` in `rule.match`:

1. Try the body first: if `body_str` parses as JSON **and** the result is a JSON object with key `key`, candidate = `body[key]`.
2. Else try the query string: if `key in query_params`, candidate = `query_params[key]`.
3. Else: rule does **not** match (key not present in either).
4. Compare: `str(candidate) == str(expected_value)` (case-sensitive).
5. Any failed key → rule does not match; try the next rule.

### Body parse failure

- Body is not valid JSON → treat body as empty for matching. Query fallback still works.
- Body is valid JSON but not an object (e.g. `[1,2,3]` or `"hello"` or `null`) → treat body as empty for matching.
- Body is `null` JSON literal → treat body as empty.

### Array values in body

For a `match` key whose value in the body is an array, the candidate is the stringified array (e.g. `"[1, 2, 3]"` via Python's `str()` on the list). This means a YAML `match: { ids: "[1, 2, 3]" }` (a deliberately-written stringified form) is the only way to match. In practice, array values in bodies effectively never match — but the behavior is fully defined and consistent (stringify both sides, exact compare).

### Interaction with auth (per-rule check order)

In the middleware:

```
1. rule = match_rule(method, path, query_params, body_str)   # path + method + match
2. if rule is not None and rule.auth is not None: evaluate_auth(...)   # only if step 1 matched
3. respond
```

`match` is checked first, in the matcher. `auth` is checked after, on the matched rule. If `match` fails for a rule, its `auth` block is never evaluated for that rule.

### Backward compatibility

- `match_rule` signature change: the two existing call sites (middleware + a few tests) are updated to pass `query_params` and `body_str`. For rules with no `match` block, this is a no-op.
- `responses.yaml` files with no `match` key on any rule: unchanged behavior.
- API responses (`/__requests`, `/__config`): `match` field is included in `/__config` rule output (nullable). No new fields on `StoredRequest`.

## API Changes

| Endpoint | Change |
|---|---|
| `GET /__config` | Each rule now includes `match` (nullable) |
| `GET /__requests` | Unchanged |
| `POST /__config/reload` | May now return `400` if any rule has a non-string `match` value (same strict-mode posture as the auth feature) |
| Everything else | Unchanged |

## Frontend Changes

**None.** The Requests table already shows the response body and headers, which is enough to verify "the right JWT came back". No new column, no new section.

The Config tab is a raw JSON dump in a `<pre>`; the new `match` field appears there automatically.

## Testing

**`tests/test_config.py`** — extend with validation:

- Load a rule with `match: { user_id: "u_emp_001" }` → succeeds
- Load a rule with `match: {}` → succeeds (no-op)
- `match: { count: 5 }` (unquoted int) → `ValidationError`
- `match: { user_id: ~ }` (YAML null) → `ValidationError`
- Rule with both `match` and `auth` → loads

**`tests/test_main.py`** — extend with behavior:

- Body match: `{user_id: u_emp_001}` + `match: { user_id: u_emp_001 }` → returns rule body
- Body miss: `{user_id: u_emp_002}` + `match: { user_id: u_emp_001 }` → falls through
- **Per-user JWT** (the headline use case): two rules same path, different `match.user_id` → request gets the right JWT
- Query match: `?user_id=u_emp_001` + `match: { user_id: u_emp_001 }` → matches
- Body wins on conflict: query `?user_id=query_val` + body `{user_id: body_val}` + `match: { user_id: body_val }` → matches
- AND semantics: two `match` keys, one hits, one doesn't → rule doesn't match
- No `match` field → matches as before (backward compat)
- Body parse failure (e.g. `Content-Type: text/plain` with non-JSON body) → query fallback still matches
- Body is valid JSON but an array (`[1,2,3]`) → body treated as empty for matching
- Array value in body for match key (`{"ids": [1,2,3]}` + `match: { ids: "[1, 2, 3]" }`) → matches with deliberate stringified form
- `path_pattern` rule with `match` → both must pass
- Combined with auth: `match` + `auth` rule → both must pass; auth evaluated only after match
- First-match-wins in YAML order

No new test framework, fixtures, or files. Extend the existing `client` fixture and patterns. Estimated **~14 new test cases** (5 in `test_config.py`, 9 in `test_main.py`).

## Backward Compatibility

- Rules without `match` work unchanged.
- `match_rule` signature change is internal. No external callers.
- `GET /__config` response gains one new key per rule (`match`), nullable. UI consumers can ignore it.
- No change to `StoredRequest`.

## Files Touched

| File | Change |
|---|---|
| `app/models.py` | Add `match: Optional[dict[str, str]] = None` to `ResponseRule` |
| `app/config.py` | Update `match_rule(method, path)` → `match_rule(method, path, query_params, body_str)` with per-key body-then-query lookup and stringify compare |
| `app/main.py` | Update the one `match_rule` call site to pass `query_params` and `body_str` (already read in the middleware) |
| `app/templates/index.html` | No change |
| `tests/test_config.py` | Add validation tests (~5 cases) |
| `tests/test_main.py` | Add behavior tests (~9 cases); update existing `match_rule` calls to pass `query_params` and `body_str` |
| `README.md` | Add a "Per-Rule Conditional Match" subsection to "Response Rules" with the JWT example |
| `responses.yaml.example` | Add a per-user-JWT example (two rules, different `match.user_id`) |

## Relationship to the Auth Feature

This spec is a **separate, additive** feature. The auth feature (in `2026-06-24-auth-header-support-design.md`) and this one both extend `ResponseRule` with new optional fields, but they are orthogonal:

- `auth` matches a request header against a fixed value list
- `match` matches a request query/body key against a fixed value

A rule can have either, both, or neither. The order of evaluation in the middleware is: `path + method + match` first (in `match_rule`), then `auth` (on the matched rule).

Implementation-wise, both features will likely ship in the same PR since they touch the same files (`app/models.py`, `app/main.py`, tests, README, example). The specs are separate for review clarity.

## Open Questions

None. All design decisions resolved during brainstorming.
