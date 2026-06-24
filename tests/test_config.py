import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.auth import evaluate_auth
from app.config import load_config, match_rule, reload_config, get_rules
from app.models import ResponseRule


SAMPLE_YAML = """
rules:
  - path: /api/hello
    method: GET
    status_code: 200
    headers:
      Content-Type: application/json
    body: '{"msg":"hello"}'

  - path_pattern: /api/users/.*
    method: GET
    status_code: 200
    body: '{"users":[]}'

  - path: /api/echo
    method: POST
    status_code: 201
    body: '{"echo":true}'

  - path: /api/slow
    delay_ms: 3000
    status_code: 200
    body: '{"slow":true}'
"""


def test_load_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        rules = load_config(path)
        assert len(rules) == 4
        assert rules[0].path == "/api/hello"
        assert rules[0].method == "GET"
        assert rules[2].status_code == 201
        assert rules[3].delay_ms == 3000
    finally:
        Path(path).unlink()


def test_reload_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        assert len(get_rules()) == 4
        Path(path).write_text("rules:\n  - path: /other\n")
        reload_config()
        assert len(get_rules()) == 1
        assert get_rules()[0].path == "/other"
    finally:
        Path(path).unlink()


def test_match_exact_path():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        rule = match_rule("GET", "/api/hello", {}, None)
        assert rule is not None
        assert rule.status_code == 200
        assert rule.body == '{"msg":"hello"}'
    finally:
        Path(path).unlink()


def test_match_path_pattern():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        rule = match_rule("GET", "/api/users/42", {}, None)
        assert rule is not None
        rule2 = match_rule("GET", "/api/users/abc/def", {}, None)
        assert rule2 is not None
    finally:
        Path(path).unlink()


def test_match_method_filter():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        rule = match_rule("POST", "/api/echo", {}, None)
        assert rule is not None
        rule2 = match_rule("GET", "/api/echo", {}, None)
        assert rule2 is None
    finally:
        Path(path).unlink()


def test_no_match():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        assert match_rule("DELETE", "/api/nonexistent", {}, None) is None
    finally:
        Path(path).unlink()


def test_empty_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("rules: []")
        path = f.name
    try:
        rules = load_config(path)
        assert rules == []
    finally:
        Path(path).unlink()


def test_missing_file():
    rules = load_config("/tmp/nonexistent_file.yaml")
    assert rules == []


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


def test_load_rule_with_auth_failure_3xx_allowed():
    rule = ResponseRule(
        path="/x", method="GET",
        auth={"header": "X-API-Key", "values": ["a"]},
        on_auth_failure={"status_code": 301, "body": "{}"},
        body="{}",
    )
    assert rule.on_auth_failure.status_code == 301


def test_load_rule_with_auth_failure_below_100_raises():
    with pytest.raises(ValidationError):
        ResponseRule(
            path="/x", method="GET",
            auth={"header": "X-API-Key", "values": ["a"]},
            on_auth_failure={"status_code": 50, "body": "{}"},
            body="{}",
        )


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
