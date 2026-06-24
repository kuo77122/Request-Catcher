# Rule Auth and Conditional Match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `ResponseRule` with two new optional fields — `auth` (require a request header) and `match` (filter by query string / body key-value pairs) — so users can protect individual mock endpoints and return different responses based on request parameters.

**Architecture:** All new logic lives in two existing files (`app/models.py`, `app/config.py`, `app/main.py`) plus one new file (`app/auth.py`). `match_rule` gains a new signature that takes the request's query params and body string; the middleware is restructured to call `match_rule` first, then `evaluate_auth` if the matched rule requires auth. Both new rule features are strict-mode Pydantic validated and surface `400` errors on bad hot-reload without wiping the previously-loaded rules.

**Tech Stack:** FastAPI, Pydantic v2, PyYAML, pytest, httpx (TestClient).

**Spec docs:**
- `docs/superpowers/specs/2026-06-24-auth-header-support-design.md`
- `docs/superpowers/specs/2026-06-24-conditional-match-by-query-and-body-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/models.py` | Pydantic models for rules and stored requests | Add `AuthConfig`, `AuthFailureResponse`; extend `ResponseRule` (`auth`, `on_auth_failure`, `match`) and `StoredRequest` (`auth_status`, `auth_header`, `auth_values_count`); add `model_validator` |
| `app/auth.py` | **New.** Pure helper: header name + value → auth result | Add `evaluate_auth` |
| `app/config.py` | Rule loading and matching | Update `match_rule` signature to take `query_params` and `body_str`; add body parsing + per-key lookup with body-wins-then-query; add stringified compare |
| `app/main.py` | FastAPI app + middleware + endpoints | Restructure middleware (call `match_rule` first, then `evaluate_auth`); wrap `/__config/reload` with try/except for `ValidationError`; include new fields in `/__config` GET |
| `app/templates/index.html` | Single-file UI | Add auth-status dot per row + Auth section in captured request detail row |
| `tests/test_config.py` | Config loading / validation tests | Add ~11 tests covering `auth`, `on_auth_failure`, `match` validation; update existing `match_rule` calls to new signature |
| `tests/test_main.py` | End-to-end behavior tests | Add ~19 tests for auth, match, combined, capture, reload error, `/__config` exposure |
| `README.md` | User-facing docs | Add "Per-Rule Auth" and "Per-Rule Conditional Match" subsections |
| `responses.yaml.example` | Example config | Add example rules for both features |

Tests are colocated with code (no new test files). The autouse `cleanup` fixture in `tests/conftest.py` already clears `_request_store` and `_rules` between tests, so all new tests are automatically isolated.

---

## Task 1: Add AuthConfig and AuthFailureResponse to models

**Files:**
- Modify: `app/models.py:1-24`
- Test: `tests/test_config.py` (append to end)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError
from app.models import ResponseRule


AUTH_VALID_YAML = """
rules:
  - path: /api/secret
    method: GET
    auth:
      header: X-API-Key
      values: ["alpha"]
    body: '{}'
"""

AUTH_AND_FAILURE_YAML = """
rules:
  - path: /api/admin
    method: POST
    auth:
      header: X-API-Key
      values: ["alpha", "beta"]
    on_auth_failure:
      status_code: 403
      body: '{"error":"forbidden"}'
    body: '{}'
"""


def test_load_rule_with_auth():
    from app.config import load_config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(AUTH_VALID_YAML)
        path = f.name
    try:
        rules = load_config(path)
        assert len(rules) == 1
        assert rules[0].auth is not None
        assert rules[0].auth.header == "X-API-Key"
        assert rules[0].auth.values == ["alpha"]
    finally:
        Path(path).unlink()


def test_load_rule_with_auth_and_on_auth_failure():
    from app.config import load_config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(AUTH_AND_FAILURE_YAML)
        path = f.name
    try:
        rules = load_config(path)
        assert rules[0].on_auth_failure is not None
        assert rules[0].on_auth_failure.status_code == 403
    finally:
        Path(path).unlink()


def test_load_rule_with_empty_auth_values_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="GET",
            auth={"header": "X-API-Key", "values": []},
            body="{}",
        )


def test_load_rule_with_empty_auth_header_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="GET",
            auth={"header": "", "values": ["a"]},
            body="{}",
        )


def test_load_rule_with_on_auth_failure_without_auth_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="GET",
            on_auth_failure={"status_code": 403, "body": "{}"},
            body="{}",
        )


def test_load_rule_with_invalid_auth_failure_status_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="GET",
            auth={"header": "X-API-Key", "values": ["a"]},
            on_auth_failure={"status_code": 999, "body": "{}"},
            body="{}",
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "test_load_rule_with_auth or test_load_rule_with_empty_auth_values_raises or test_load_rule_with_empty_auth_header_raises or test_load_rule_with_on_auth_failure_without_auth_raises or test_load_rule_with_invalid_auth_failure_status_raises or test_load_rule_with_auth_and_on_auth_failure" -v`

Expected: All 6 fail with `ImportError` (no `AuthConfig`/`AuthFailureResponse`) or `ValidationError` not raised (no model yet).

- [ ] **Step 3: Implement AuthConfig and AuthFailureResponse in models.py**

Replace `app/models.py` with:

```python
from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from typing import Optional, Union


class AuthConfig(BaseModel):
    header: str
    values: list[str]


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
    match: Optional[dict[str, str]] = None

    @model_validator(mode="after")
    def _on_auth_failure_requires_auth(self):
        if self.on_auth_failure is not None and self.auth is None:
            raise ValueError("on_auth_failure requires auth to be set")
        return self


class StoredRequest(BaseModel):
    id: int
    timestamp: datetime
    method: str
    path: str
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None
    client_host: Optional[str] = None
    auth_status: Optional[str] = None
    auth_header: Optional[str] = None
    auth_values_count: Optional[int] = None
```

Note: `match` is included here for Task 2's tests to pass; that task adds tests for it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k "test_load_rule_with_auth or test_load_rule_with_empty_auth_values_raises or test_load_rule_with_empty_auth_header_raises or test_load_rule_with_on_auth_failure_without_auth_raises or test_load_rule_with_invalid_auth_failure_status_raises or test_load_rule_with_auth_and_on_auth_failure" -v`

Expected: 6 passed.

- [ ] **Step 5: Run the full test suite to make sure nothing regressed**

Run: `uv run pytest -v`

Expected: All existing tests pass; 6 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_config.py
git commit -m "feat(models): add AuthConfig, AuthFailureResponse, match field, StoredRequest auth fields"
```

---

## Task 2: Add match field validation tests and confirm

The `match` field was already added to `ResponseRule` in Task 1. This task adds tests for it.

**Files:**
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
MATCH_VALID_YAML = """
rules:
  - path: /api/x
    method: POST
    match:
      user_id: u_emp_001
    body: '{}'
"""


def test_load_rule_with_match():
    from app.config import load_config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(MATCH_VALID_YAML)
        path = f.name
    try:
        rules = load_config(path)
        assert rules[0].match == {"user_id": "u_emp_001"}
    finally:
        Path(path).unlink()


def test_load_rule_with_empty_match():
    rule = ResponseRule(path="/x", method="GET", match={}, body="{}")
    assert rule.match == {}


def test_load_rule_with_match_non_string_value_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="POST",
            match={"count": 5},
            body="{}",
        )


def test_load_rule_with_match_null_value_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="POST",
            match={"user_id": None},
            body="{}",
        )


def test_load_rule_with_match_and_auth():
    rule = ResponseRule(
        path="/x", method="GET",
        auth={"header": "X-API-Key", "values": ["k"]},
        match={"user_id": "u_emp_001"},
        body="{}",
    )
    assert rule.auth is not None
    assert rule.match == {"user_id": "u_emp_001"}
```

- [ ] **Step 2: Run the tests to verify they pass (model from Task 1 should make them green)**

Run: `uv run pytest tests/test_config.py -k "test_load_rule_with_match or test_load_rule_with_empty_match or test_load_rule_with_match_non_string_value_raises or test_load_rule_with_match_null_value_raises or test_load_rule_with_match_and_auth" -v`

Expected: 5 passed (model field is in place from Task 1).

- [ ] **Step 3: Run the full test suite to confirm no regression**

Run: `uv run pytest -v`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_config.py
git commit -m "test(config): cover match field validation"
```

---

## Task 3: Build evaluate_auth helper

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_config.py` (append — auth helper tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from app.auth import evaluate_auth
from app.models import ResponseRule


def _rule_with_auth(header: str, values: list[str]) -> ResponseRule:
    return ResponseRule(
        path="/x", method="GET",
        auth={"header": header, "values": values},
        body="{}",
    )


def test_evaluate_auth_no_rule():
    assert evaluate_auth(None, {}) == (None, None, None)


def test_evaluate_auth_rule_without_auth():
    rule = ResponseRule(path="/x", method="GET", body="{}")
    assert evaluate_auth(rule, {}) == (None, None, None)


def test_evaluate_auth_header_missing():
    rule = _rule_with_auth("X-API-Key", ["alpha"])
    status, hdr, cnt = evaluate_auth(rule, {"content-type": "application/json"})
    assert status == "missing"
    assert hdr == "X-API-Key"
    assert cnt == 1


def test_evaluate_auth_header_present_valid():
    rule = _rule_with_auth("X-API-Key", ["alpha", "beta"])
    status, hdr, cnt = evaluate_auth(rule, {"x-api-key": "alpha"})
    assert status == "ok"
    assert hdr == "X-API-Key"
    assert cnt == 2


def test_evaluate_auth_header_present_invalid():
    rule = _rule_with_auth("X-API-Key", ["alpha"])
    status, hdr, cnt = evaluate_auth(rule, {"x-api-key": "wrong"})
    assert status == "invalid"
    assert hdr == "X-API-Key"
    assert cnt == 1


def test_evaluate_auth_case_insensitive_name():
    rule = _rule_with_auth("X-API-Key", ["alpha"])
    status, _, _ = evaluate_auth(rule, {"X-Api-Key": "alpha"})
    assert status == "ok"


def test_evaluate_auth_case_sensitive_value():
    rule = _rule_with_auth("X-API-Key", ["alpha"])
    status, _, _ = evaluate_auth(rule, {"x-api-key": "ALPHA"})
    assert status == "invalid"


def test_evaluate_auth_empty_value_is_invalid():
    rule = _rule_with_auth("X-API-Key", ["alpha"])
    status, _, _ = evaluate_auth(rule, {"x-api-key": ""})
    assert status == "invalid"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "test_evaluate_auth" -v`

Expected: All 8 fail with `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 3: Create app/auth.py**

```python
from typing import Optional
from app.models import ResponseRule


def evaluate_auth(
    rule: Optional[ResponseRule], request_headers: dict[str, str]
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return (auth_status, auth_header, auth_values_count) for a request.

    Status values:
      - None: rule is None, or rule has no `auth` block
      - "ok": header present and value is in rule.auth.values
      - "missing": header absent (case-insensitive name lookup)
      - "invalid": header present but value not in rule.auth.values
                 (or value is empty string)

    `auth_header` and `auth_values_count` are populated only when status
    is "ok" | "missing" | "invalid". They are None when no auth check
    was performed.
    """
    if rule is None or rule.auth is None:
        return None, None, None

    expected_header = rule.auth.header
    lowered_request = {k.lower(): v for k, v in request_headers.items()}

    if expected_header.lower() not in lowered_request:
        return "missing", expected_header, len(rule.auth.values)

    actual = lowered_request[expected_header.lower()]
    if actual not in rule.auth.values:
        return "invalid", expected_header, len(rule.auth.values)

    return "ok", expected_header, len(rule.auth.values)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k "test_evaluate_auth" -v`

Expected: 8 passed.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/auth.py tests/test_config.py
git commit -m "feat(auth): add evaluate_auth helper"
```

---

## Task 4: Update match_rule with body/query match logic

The current `match_rule(method, path)` only knows about path/method. We need it to also know about `query_params` and `body_str` so it can evaluate the new `match` block. The signature change breaks existing tests — they get updated in this task.

**Files:**
- Modify: `app/config.py:31-39` — new `match_rule` signature, add helper functions
- Test: `tests/test_config.py` (replace existing `test_match_*` tests; add new ones)

- [ ] **Step 1: Read the current match_rule tests to understand what to update**

Read `tests/test_config.py` lines 63-113 (the four `test_match_*` functions) to see the call sites. They all call `match_rule("GET", "/api/hello")` — these need to be updated to `match_rule("GET", "/api/hello", {}, None)`.

- [ ] **Step 2: Update existing match_rule tests to new signature**

In `tests/test_config.py`, change each existing call:
- `match_rule("GET", "/api/hello")` → `match_rule("GET", "/api/hello", {}, None)`
- `match_rule("GET", "/api/users/42")` → `match_rule("GET", "/api/users/42", {}, None)`
- `match_rule("GET", "/api/users/abc/def")` → `match_rule("GET", "/api/users/abc/def", {}, None)`
- `match_rule("POST", "/api/echo")` → `match_rule("POST", "/api/echo", {}, None)`
- `match_rule("GET", "/api/echo")` → `match_rule("GET", "/api/echo", {}, None)`
- `match_rule("DELETE", "/api/nonexistent")` → `match_rule("DELETE", "/api/nonexistent", {}, None)`

- [ ] **Step 3: Run the existing tests to verify they fail (signature mismatch)**

Run: `uv run pytest tests/test_config.py -v`

Expected: `test_match_exact_path`, `test_match_path_pattern`, `test_match_method_filter`, `test_no_match` all fail with a `TypeError` (missing positional arguments).

- [ ] **Step 4: Add new match-related tests**

Append to `tests/test_config.py`:

```python
MATCH_RULES_YAML = """
rules:
  - path: /api/auth/token
    method: POST
    match:
      user_id: u_emp_001
    body: '{"token":"alice"}'

  - path: /api/auth/token
    method: POST
    match:
      user_id: u_emp_002
    body: '{"token":"bob"}'

  - path: /api/auth/token
    method: POST
    body: '{"token":"default"}'

  - path_pattern: /api/identity/resolve.*
    method: GET
    match:
      platform: line
    body: '{"platform":"line"}'
"""


def _load_match_rules():
    from app.config import load_config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(MATCH_RULES_YAML)
        path = f.name
    load_config(path)
    return path


def test_match_body_match_succeeds():
    path = _load_match_rules()
    try:
        rule = match_rule("POST", "/api/auth/token", {}, '{"user_id":"u_emp_001"}')
        assert rule is not None
        assert "alice" in rule.body
    finally:
        Path(path).unlink()


def test_match_body_match_falls_through():
    path = _load_match_rules()
    try:
        rule = match_rule("POST", "/api/auth/token", {}, '{"user_id":"u_emp_999"}')
        assert rule is not None
        assert '"default"' in rule.body
    finally:
        Path(path).unlink()


def test_match_per_user_first_wins():
    path = _load_match_rules()
    try:
        rule = match_rule("POST", "/api/auth/token", {}, '{"user_id":"u_emp_002"}')
        assert rule is not None
        assert "bob" in rule.body
    finally:
        Path(path).unlink()


def test_match_query_string_succeeds():
    path = _load_match_rules()
    try:
        rule = match_rule(
            "GET", "/api/identity/resolve", {"platform": "line"}, None
        )
        assert rule is not None
    finally:
        Path(path).unlink()


def test_match_query_string_fails():
    path = _load_match_rules()
    try:
        rule = match_rule(
            "GET", "/api/identity/resolve", {"platform": "facebook"}, None
        )
        assert rule is None
    finally:
        Path(path).unlink()


def test_match_body_wins_over_query():
    path = _load_match_rules()
    try:
        # body has user_id=u_emp_001, query has user_id=other
        # match requires user_id=u_emp_001 -> body wins
        rule = match_rule(
            "POST", "/api/auth/token",
            {"user_id": "other"},
            '{"user_id":"u_emp_001"}',
        )
        assert rule is not None
        assert "alice" in rule.body
    finally:
        Path(path).unlink()


def test_match_and_both_keys_required():
    yaml = """
rules:
  - path: /api/x
    method: GET
    match:
      a: "1"
      b: "2"
    body: '{}'
"""
    from app.config import load_config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml)
        path = f.name
    try:
        load_config(path)
        # both keys hit
        assert match_rule("GET", "/api/x", {}, '{"a":"1","b":"2"}') is not None
        # only one key hits
        assert match_rule("GET", "/api/x", {}, '{"a":"1"}') is None
        # neither key hits
        assert match_rule("GET", "/api/x", {}, '{"a":"3","b":"4"}') is None
    finally:
        Path(path).unlink()


def test_match_body_parse_failure_falls_back_to_query():
    from app.config import load_config
    yaml = """
rules:
  - path: /api/x
    method: GET
    match:
      user_id: u_emp_001
    body: '{}'
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml)
        path = f.name
    try:
        load_config(path)
        # body is not JSON; query should still match
        assert match_rule("GET", "/api/x", {"user_id": "u_emp_001"}, "not-json") is not None
    finally:
        Path(path).unlink()


def test_match_body_array_value_stringified_exact():
    from app.config import load_config
    yaml = """
rules:
  - path: /api/x
    method: GET
    match:
      ids: "[1, 2, 3]"
    body: '{}'
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml)
        path = f.name
    try:
        load_config(path)
        # stringified Python list form
        assert match_rule("GET", "/api/x", {}, '{"ids":[1,2,3]}') is not None
        # different array doesn't match
        assert match_rule("GET", "/api/x", {}, '{"ids":[4,5,6]}') is None
    finally:
        Path(path).unlink()


def test_match_path_pattern_with_match():
    path = _load_match_rules()
    try:
        # path_pattern + match both must pass
        assert match_rule(
            "GET", "/api/identity/resolve?ignored=1",
            {"platform": "line"}, None
        ) is not None
        # pattern matches but match fails
        assert match_rule(
            "GET", "/api/identity/resolve?ignored=1",
            {"platform": "web"}, None
        ) is None
    finally:
        Path(path).unlink()


def test_match_no_block_backward_compat():
    # a rule with no match field still matches on path/method alone
    from app.config import load_config
    yaml = """
rules:
  - path: /api/x
    method: GET
    body: '{}'
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml)
        path = f.name
    try:
        load_config(path)
        # any query/body — still matches because no match block
        assert match_rule("GET", "/api/x", {"a": "b"}, '{"c":"d"}') is not None
        assert match_rule("GET", "/api/x", {}, None) is not None
    finally:
        Path(path).unlink()
```

- [ ] **Step 5: Run the new tests to verify they fail (old match_rule signature)**

Run: `uv run pytest tests/test_config.py -k "test_match_body or test_match_query or test_match_per_user or test_match_and or test_match_body_parse or test_match_body_array or test_match_path_pattern_with or test_match_no_block or test_match_body_wins" -v`

Expected: All fail with `TypeError: match_rule() missing 2 required positional arguments`.

- [ ] **Step 6: Update match_rule in app/config.py**

Replace the contents of `app/config.py` with:

```python
import json
import re
import yaml
from pathlib import Path
from typing import Optional
from app.models import ResponseRule

_config_path = Path("responses.yaml")
_rules: list[ResponseRule] = []


def load_config(path: str | Path | None = None) -> list[ResponseRule]:
    global _config_path, _rules
    if path:
        _config_path = Path(path)
    if not _config_path.exists():
        _rules = []
        return _rules
    raw = _config_path.read_text()
    data = yaml.safe_load(raw) or {}
    _rules = [ResponseRule(**r) for r in data.get("rules", [])]
    return _rules


def reload_config() -> list[ResponseRule]:
    return load_config()


def get_rules() -> list[ResponseRule]:
    return _rules


def match_rule(
    method: str,
    path: str,
    query_params: dict[str, str],
    body_str: Optional[str],
) -> Optional[ResponseRule]:
    for rule in _rules:
        if rule.method and rule.method.upper() != method.upper():
            continue
        path_matched = False
        if rule.path and rule.path == path:
            path_matched = True
        elif rule.path_pattern and re.search(rule.path_pattern, path):
            path_matched = True
        if not path_matched:
            continue
        if not _match_conditions(rule, query_params, body_str):
            continue
        return rule
    return None


def _match_conditions(
    rule: ResponseRule,
    query_params: dict[str, str],
    body_str: Optional[str],
) -> bool:
    if not rule.match:
        return True
    body = _parse_body_object(body_str)
    for key, expected in rule.match.items():
        if body is not None and key in body:
            candidate = body[key]
        elif key in query_params:
            candidate = query_params[key]
        else:
            return False
        if str(candidate) != str(expected):
            return False
    return True


def _parse_body_object(body_str: Optional[str]) -> Optional[dict]:
    if not body_str:
        return None
    try:
        body = json.loads(body_str)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    return body
```

- [ ] **Step 7: Run the full test_config suite to verify all pass**

Run: `uv run pytest tests/test_config.py -v`

Expected: All tests in `test_config.py` pass.

- [ ] **Step 8: Run the full test suite to check for regressions**

Run: `uv run pytest -v`

Expected: All test_main.py tests should still pass (they call `match_rule` indirectly via the middleware, but the middleware signature hasn't been updated yet — those tests may break; that's fine, we fix in Task 5).

If `test_main.py` tests fail because of `match_rule` signature change, note the failures and proceed. They'll be fixed in Task 5.

- [ ] **Step 9: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(config): match_rule evaluates query/body match block"
```

---

## Task 5: Restructure middleware for auth + match + capture

This is the biggest change: the middleware now reads body, runs `match_rule`, runs `evaluate_auth` (if rule has auth), captures the request with auth fields, then responds.

**Files:**
- Modify: `app/main.py:1-52`
- Test: `tests/test_main.py` (append auth/match integration tests)

- [ ] **Step 1: Read app/main.py and app/templates/index.html to understand the current shape**

Already known from spec exploration. The middleware is at `app/main.py:22-52`, and `app/main.py:108-118` has the catch-all handler that should be deleted (the middleware now always handles the response).

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_main.py`:

```python
AUTH_MATCH_YAML = """
rules:
  - path: /api/secret
    method: GET
    auth:
      header: X-API-Key
      values: ["alpha", "beta"]
    body: '{"secret":"data"}'
    headers:
      Content-Type: application/json

  - path: /api/secret-admin
    method: GET
    auth:
      header: X-API-Key
      values: ["admin"]
    on_auth_failure:
      status_code: 403
      body: '{"error":"forbidden"}'
      headers:
        Content-Type: application/json
    body: '{"role":"admin"}'

  - path: /api/auth/token
    method: POST
    match:
      user_id: u_emp_001
    body: '{"token":"alice"}'

  - path: /api/auth/token
    method: POST
    match:
      user_id: u_emp_002
    body: '{"token":"bob"}'

  - path: /api/auth/token
    method: POST
    body: '{"token":"default"}'

  - path: /api/both
    method: GET
    match:
      x: "1"
    auth:
      header: X-API-Key
      values: ["alpha"]
    body: '{"ok":true}'
"""


def _load_yaml_in_main(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    load_config(f.name)
    return f.name


def test_auth_valid_header_returns_normal(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/secret", headers={"X-API-Key": "alpha"})
        assert resp.status_code == 200
        assert resp.json() == {"secret": "data"}
    finally:
        Path(path).unlink()


def test_auth_missing_header_returns_401(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/secret")
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}
    finally:
        Path(path).unlink()


def test_auth_invalid_value_returns_401(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/secret", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401
    finally:
        Path(path).unlink()


def test_auth_custom_failure_response(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/secret-admin", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 403
        assert resp.json() == {"error": "forbidden"}
    finally:
        Path(path).unlink()


def test_auth_header_name_case_insensitive(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/secret", headers={"x-api-key": "alpha"})
        assert resp.status_code == 200
    finally:
        Path(path).unlink()


def test_auth_value_case_sensitive(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/secret", headers={"X-API-Key": "ALPHA"})
        assert resp.status_code == 401
    finally:
        Path(path).unlink()


def test_captured_request_records_auth_status_ok(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        client.get("/api/secret", headers={"X-API-Key": "alpha"})
        data = client.get("/__requests").json()
        assert data[0]["auth_status"] == "ok"
        assert data[0]["auth_header"] == "X-API-Key"
        assert data[0]["auth_values_count"] == 2
    finally:
        Path(path).unlink()


def test_captured_request_records_auth_status_missing(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        client.get("/api/secret")
        data = client.get("/__requests").json()
        assert data[0]["auth_status"] == "missing"
        assert data[0]["auth_header"] == "X-API-Key"
    finally:
        Path(path).unlink()


def test_captured_request_records_auth_status_invalid(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        client.get("/api/secret", headers={"X-API-Key": "wrong"})
        data = client.get("/__requests").json()
        assert data[0]["auth_status"] == "invalid"
    finally:
        Path(path).unlink()


def test_match_per_user_returns_correct_jwt(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp_alice = client.post(
            "/api/auth/token", json={"user_id": "u_emp_001"}
        )
        resp_bob = client.post(
            "/api/auth/token", json={"user_id": "u_emp_002"}
        )
        resp_other = client.post(
            "/api/auth/token", json={"user_id": "u_emp_999"}
        )
        assert resp_alice.json() == {"token": "alice"}
        assert resp_bob.json() == {"token": "bob"}
        assert resp_other.json() == {"token": "default"}
    finally:
        Path(path).unlink()


def test_match_query_string(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        resp = client.get("/api/identity/resolve?platform=line")
        # No rule for that path in AUTH_MATCH_YAML, falls through to catch-all
        assert resp.status_code == 200
    finally:
        Path(path).unlink()


def test_match_no_match_block_works_as_before(client: TestClient):
    # A rule without match fires on path/method only
    yaml = """
rules:
  - path: /api/anything
    method: POST
    body: '{"ok":true}'
"""
    path = _load_yaml_in_main(yaml)
    try:
        resp = client.post("/api/anything", json={"any": "data"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        Path(path).unlink()


def test_match_and_auth_combined_both_required(client: TestClient):
    path = _load_yaml_in_main(AUTH_MATCH_YAML)
    try:
        # match hits, auth valid -> 200
        resp_ok = client.get(
            "/api/both?x=1", headers={"X-API-Key": "alpha"}
        )
        assert resp_ok.status_code == 200
        # match hits, auth invalid -> 401
        resp_no_auth = client.get("/api/both?x=1")
        assert resp_no_auth.status_code == 401
        # match misses, rule does not match -> falls through to catch-all
        resp_no_match = client.get(
            "/api/both?x=2", headers={"X-API-Key": "alpha"}
        )
        # catch-all returns 200 with the JSON acknowledgment
        assert resp_no_match.status_code == 200
        assert "no rule matched" in resp_no_match.json()["message"]
    finally:
        Path(path).unlink()


def test_captured_request_no_rule_matched_auth_status_null(client: TestClient):
    client.get("/no-rule-here")
    data = client.get("/__requests").json()
    assert data[0]["auth_status"] is None
    assert data[0]["auth_header"] is None
    assert data[0]["auth_values_count"] is None


def test_captured_request_rule_without_auth_status_null(client: TestClient):
    yaml = """
rules:
  - path: /api/anything
    method: POST
    body: '{}'
"""
    path = _load_yaml_in_main(yaml)
    try:
        client.post("/api/anything", json={})
        data = client.get("/__requests").json()
        assert data[0]["auth_status"] is None
    finally:
        Path(path).unlink()
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_main.py -k "test_auth_ or test_captured_request_records_auth_status or test_match_per_user or test_match_query_string or test_match_no_match_block_works_as_before or test_match_and_auth_combined_both_required or test_captured_request_no_rule_matched_auth_status_null or test_captured_request_rule_without_auth_status_null" -v`

Expected: All fail. The middleware is still doing the old `match_rule(method, path)` call with the wrong signature, so the app will likely 500 or behave wrong.

- [ ] **Step 4: Update the middleware in app/main.py**

Replace the imports and the middleware in `app/main.py`. Replace the entire file with:

```python
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.models import StoredRequest
from app.storage import store_request, get_requests, clear_requests, get_request_by_id
from app.config import load_config, reload_config, match_rule, get_rules
from app.auth import evaluate_auth

HERE = Path(__file__).parent
TEMPLATE_PATH = HERE / "templates" / "index.html"
MANAGEMENT_PREFIXES = ("/__",)

app = FastAPI(title="Request Catcher")

load_config()


@app.middleware("http")
async def capture_and_intercept(request: Request, call_next):
    path = request.url.path
    is_management = any(path.startswith(p) for p in MANAGEMENT_PREFIXES)

    if is_management:
        return await call_next(request)

    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else None

    rule = match_rule(
        request.method, path, dict(request.query_params), body_str
    )
    auth_status, auth_header, auth_values_count = evaluate_auth(
        rule, dict(request.headers)
    )

    sr = StoredRequest(
        id=0,
        timestamp=datetime.now(),
        method=request.method,
        path=path,
        query_params=dict(request.query_params),
        headers=dict(request.headers),
        body=body_str,
        client_host=request.client.host if request.client else None,
        auth_status=auth_status,
        auth_header=auth_header,
        auth_values_count=auth_values_count,
    )
    store_request(sr)

    if rule is None:
        return JSONResponse(
            {
                "message": "Request captured but no rule matched. Add a rule to responses.yaml.",
                "path": path,
                "method": request.method,
            }
        )

    if auth_status in ("missing", "invalid"):
        if rule.on_auth_failure is not None:
            return Response(
                content=rule.on_auth_failure.body,
                status_code=rule.on_auth_failure.status_code,
                headers=rule.on_auth_failure.headers,
            )
        # defaults
        return Response(
            content='{"error":"unauthorized"}',
            status_code=401,
            headers={"Content-Type": "application/json"},
        )

    if rule.delay_ms > 0:
        await asyncio.sleep(rule.delay_ms / 1000)
    return Response(
        content=rule.body,
        status_code=rule.status_code,
        headers=rule.headers,
    )


@app.get("/__requests")
async def list_requests():
    return JSONResponse(
        [r.model_dump(mode="json") for r in get_requests()]
    )


@app.get("/__requests/{request_id}")
async def get_request(request_id: int):
    r = get_request_by_id(request_id)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(r.model_dump(mode="json"))


@app.delete("/__requests")
async def delete_requests():
    clear_requests()
    return JSONResponse({"ok": True})


@app.get("/__config")
async def get_config():
    return JSONResponse(
        {
            "rules": [
                {
                    "path": r.path,
                    "path_pattern": r.path_pattern,
                    "method": r.method,
                    "status_code": r.status_code,
                    "headers": r.headers,
                    "body": r.body,
                    "delay_ms": r.delay_ms,
                    "auth": r.auth.model_dump() if r.auth else None,
                    "on_auth_failure": (
                        r.on_auth_failure.model_dump()
                        if r.on_auth_failure else None
                    ),
                    "match": r.match,
                }
                for r in get_rules()
            ]
        }
    )


@app.post("/__config/reload")
async def reload_config_endpoint():
    try:
        reload_config()
    except ValidationError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "rules_count": len(get_rules())})


@app.get("/", response_class=HTMLResponse)
async def index():
    template = TEMPLATE_PATH.read_text()
    return HTMLResponse(template)
```

Note: the old `catch_all` route (`@app.api_route("/{path:path}", ...)`) is removed. The middleware now handles every non-management request, including the "no rule matched" case. Without this, the old catch-all route would still receive unmatched requests, but the middleware would have already stored them — causing the response to be generated twice. Removing the catch-all is required for the new flow.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`

Expected: All tests pass. The existing `test_captured_request_has_detail` and similar should continue to work; the new auth/match tests should all pass.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat(middleware): integrate auth check and match with capture"
```

---

## Task 6: Hot-reload error handling returns 400

`POST /__config/reload` should return 400 (not crash) when the new config has bad auth/match values, and the previously-loaded rules should stay in effect.

**Files:**
- Modify: `app/main.py` (already done in Task 5 — but verify the try/except is there)
- Test: `tests/test_main.py` (append)

- [ ] **Step 1: Verify the try/except is in place**

Read `app/main.py` lines around the `reload_config_endpoint` function (added in Task 5). The function should look like:

```python
@app.post("/__config/reload")
async def reload_config_endpoint():
    try:
        reload_config()
    except ValidationError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "rules_count": len(get_rules())})
```

If it's not there, add it.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_main.py`:

```python
def test_reload_bad_config_returns_400(client: TestClient):
    # Load a valid config first
    valid_yaml = """
rules:
  - path: /api/x
    method: GET
    body: '{}'
"""
    path = _load_yaml_in_main(valid_yaml)
    try:
        # Sanity: the rule works
        resp = client.get("/api/x")
        assert resp.status_code == 200

        # Now overwrite the file with a bad config (non-string match value)
        Path(path).write_text("""
rules:
  - path: /api/y
    method: GET
    match:
      count: 5
    body: '{}'
""")

        resp = client.post("/__config/reload")
        assert resp.status_code == 400
        assert "error" in resp.json()

        # Old rule should still be in effect
        resp = client.get("/api/x")
        assert resp.status_code == 200
    finally:
        Path(path).unlink()


def test_reload_bad_auth_returns_400(client: TestClient):
    valid_yaml = """
rules:
  - path: /api/x
    method: GET
    body: '{}'
"""
    path = _load_yaml_in_main(valid_yaml)
    try:
        Path(path).write_text("""
rules:
  - path: /api/y
    method: GET
    auth:
      header: ""
      values: ["a"]
    body: '{}'
""")
        resp = client.post("/__config/reload")
        assert resp.status_code == 400
    finally:
        Path(path).unlink()
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `uv run pytest tests/test_main.py -k "test_reload_bad_config_returns_400 or test_reload_bad_auth_returns_400" -v`

Expected: 2 passed (Task 5 already added the try/except).

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -v`

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_main.py
git commit -m "test(main): cover reload error handling"
```

---

## Task 7: Frontend — auth status dot + detail panel Auth section

The UI is a single static HTML file. The `auth_status` is per-captured-request, so the requests table gets a small dot per row, and the detail row gets a new "Auth" section.

**Files:**
- Modify: `app/templates/index.html`

- [ ] **Step 1: Add CSS for the auth-status dots**

In the `<style>` block, after the `.options` rule (around line 46), add:

```css
.auth-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-left: 6px; vertical-align: middle; }
.auth-dot.ok { background: #3fb950; }
.auth-dot.missing { background: #d29922; }
.auth-dot.invalid { background: #da3633; }
.col-auth { width: 60px; }
```

- [ ] **Step 2: Add an Auth column header to the table**

In the `<thead>` block (around line 86), add a new `<th>`:

```html
<tr><th class="col-num">#</th><th class="col-method">Method</th><th>Path</th><th class="col-auth">Auth</th><th class="col-time">Time</th></tr>
```

- [ ] **Step 3: Add the auth dot to each row + an Auth section to the detail row**

In the `renderTable` function in the `<script>` block, find the row template (around line 139) and update it. The full updated row template is:

```javascript
tbody.innerHTML = filtered.map(r => {
  const mc = r.method.toLowerCase();
  const qs = r.query_params && Object.keys(r.query_params).length ? '?' + new URLSearchParams(r.query_params).toString() : '';
  const ts = new Date(r.timestamp).toLocaleTimeString();
  const bodyPreview = r.body ? r.body.slice(0, 200) + (r.body.length > 200 ? '...' : '') : '';
  const authDot = r.auth_status
    ? `<span class="auth-dot ${r.auth_status}" title="${r.auth_status}"></span>`
    : '';
  const authSection = r.auth_status
    ? `<div class="detail-section">
        <h4>Auth</h4>
        <pre>status: ${r.auth_status}\nheader: ${r.auth_header || ''}\nvalid values: ${r.auth_values_count ?? ''}</pre>
      </div>`
    : '';
  return `<tr class="clickable" onclick="toggleDetail(${r.id})">
    <td class="req-num">${r.id}</td>
    <td><span class="method ${mc}">${r.method}</span></td>
    <td class="path">${r.path}${qs}</td>
    <td>${authDot}</td>
    <td class="time">${ts}</td>
  </tr>
  <tr class="row-detail" id="detail-${r.id}">
    <td colspan="5">
      <div class="detail-section">
        <h4>Headers</h4>
        <pre>${r.headers ? JSON.stringify(r.headers, null, 2) : '(empty)'}</pre>
      </div>
      <div class="detail-section">
        <h4>Body</h4>
        <pre>${bodyPreview || '(empty)'}</pre>
      </div>
      ${authSection}
      <div class="detail-section">
        <h4>Client</h4>
        <pre>${r.client_host || 'unknown'}</pre>
      </div>
    </td>
  </tr>`;
}).join('');
```

Note: the `colspan` changed from `4` to `5` to account for the new Auth column.

- [ ] **Step 4: Manual visual check**

Run: `uv run uvicorn app.main:app --reload`

Open `http://localhost:8000` (or the configured port — check `pyproject.toml` and `docker-compose.yml`). Send a request with and without an auth header. Verify:
- A green dot appears for valid auth
- An amber dot for missing auth
- A red dot for invalid auth
- No dot for rules with no auth
- Clicking a row opens the detail with the new "Auth" section

Stop the server with Ctrl-C.

- [ ] **Step 5: Commit**

```bash
git add app/templates/index.html
git commit -m "feat(ui): auth status indicator + detail panel section"
```

---

## Task 8: Update README and example

**Files:**
- Modify: `README.md`
- Modify: `responses.yaml.example`

- [ ] **Step 1: Add "Per-Rule Auth" section to README**

After the existing "Response Rules" section, before "Management Endpoints", add:

````markdown
## Per-Rule Auth

A rule can require a specific auth header. If the request doesn't carry it (or carries the wrong value), the request is rejected with a configurable failure response. The request is still captured and visible in the UI.

```yaml
rules:
  - path: /api/secret
    method: GET
    auth:
      header: X-API-Key
      values: ["alpha-key", "beta-key"]
    on_auth_failure:
      status_code: 401
      body: '{"error":"unauthorized"}'
    status_code: 200
    body: '{"secret":"data"}'
```

For JWT-style auth, match the full `Authorization` value (including the `Bearer` prefix):

```yaml
- path: /api/me
  method: GET
  auth:
    header: Authorization
    values: ["Bearer eyJhbGciOiJIUzI1NiJ9..."]
  status_code: 200
  body: '{"user_id":"u_emp_001"}'
```

Header name lookup is case-insensitive. Value comparison is case-sensitive. If `on_auth_failure` is omitted, defaults are `401` + `{"error":"unauthorized"}` + `Content-Type: application/json`.

## Per-Rule Conditional Match

Return different responses based on a request's query string or body values. Write one rule per scenario; first match wins.

```yaml
rules:
  - path: /api/v1/auth/token
    method: POST
    match:
      user_id: u_emp_001
    status_code: 200
    body: '{"token":"<jwt-for-alice>"}'

  - path: /api/v1/auth/token
    method: POST
    match:
      user_id: u_emp_002
    status_code: 200
    body: '{"token":"<jwt-for-bob>"}'
```

`match` values must be strings; quote numbers in YAML (`count: "5"`). Body is parsed as JSON; non-JSON bodies fall back to query-string matching. When the same key appears in both, body wins. Multiple `match` keys are AND'd.
````

- [ ] **Step 2: Add example rules to responses.yaml.example**

Append to `responses.yaml.example`:

```yaml
  # ── Examples of per-rule auth and conditional match ───────────────

  # Rule requiring an auth header (X-API-Key)
  - path: /api/secret
    method: GET
    auth:
      header: X-API-Key
      values: ["alpha-key", "beta-key"]
    body: '{"secret":"visible only with a valid key"}'

  # Per-user JWT — different match.user_id returns a different token
  - path: /api/v1/auth/token
    method: POST
    match:
      user_id: u_emp_001
    body: '{"token":"<jwt-for-alice>"}'

  - path: /api/v1/auth/token
    method: POST
    match:
      user_id: u_emp_002
    body: '{"token":"<jwt-for-bob>"}'

  # Combined: conditional match + auth
  - path: /api/v1/identity/resolve
    method: GET
    match:
      platform: line
    auth:
      header: X-API-Key
      values: ["alpha-key"]
    body: '{"user_id":"u_emp_001","platform":"line"}'
```

- [ ] **Step 3: Commit**

```bash
git add README.md responses.yaml.example
git commit -m "docs: document per-rule auth and conditional match"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`

Expected: All tests pass. New test counts: ~16 in test_config.py (existing + 11 new), ~30 in test_main.py (existing + ~19 new), unchanged in test_storage.py.

- [ ] **Step 2: Smoke test the running app**

```bash
uv run uvicorn app.main:app --reload &
SERVER_PID=$!
sleep 2
```

In another shell, or as a continuation:

```bash
# 1. Auth — missing key
curl -i http://localhost:8000/api/secret
# Expect: 401, {"error":"unauthorized"}

# 2. Auth — wrong key
curl -i -H "X-API-Key: wrong" http://localhost:8000/api/secret
# Expect: 401

# 3. Auth — valid key
curl -i -H "X-API-Key: alpha-key" http://localhost:8000/api/secret
# Expect: 200, {"secret":"..."}

# 4. Per-user JWT
curl -i -X POST -H "Content-Type: application/json" -d '{"user_id":"u_emp_001"}' http://localhost:8000/api/v1/auth/token
# Expect: 200, {"token":"<jwt-for-alice>"}

# 5. UI
# Open http://localhost:8000 in a browser. Verify:
#   - requests table has a 4th column "Auth" with colored dots
#   - clicking a row opens a detail with an "Auth" section

# 6. Cleanup
kill $SERVER_PID
```

- [ ] **Step 3: Final commit if anything was changed during smoke testing**

If the smoke test surfaced any small fixes, commit them:

```bash
git add -A
git commit -m "chore: smoke test fixes"
```

(If no changes were needed, skip this step.)

---

## Self-Review Checklist

Run through this before declaring the plan done:

- [ ] Every spec requirement has a task: auth fields on `ResponseRule` ✓ (T1), `match` field ✓ (T2), `evaluate_auth` ✓ (T3), `match_rule` with body/query ✓ (T4), middleware integration ✓ (T5), reload error handling ✓ (T6), `/__config` exposure ✓ (T5), frontend ✓ (T7), docs ✓ (T8).
- [ ] No placeholders: every code block shows the actual code, every test shows the actual test.
- [ ] Type consistency: `evaluate_auth` returns `tuple[Optional[str], Optional[str], Optional[int]]` everywhere it's mentioned; `match_rule(method, path, query_params, body_str)` signature is consistent across tasks.
- [ ] Test isolation: the autouse `cleanup` fixture in `tests/conftest.py` clears state between tests, so the new tests in T2, T5, T6 (which load YAML in a `try/finally`) are properly isolated.
- [ ] The middleware restructure removes the old `catch_all` route — the new middleware handles all non-management paths.
