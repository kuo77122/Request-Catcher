import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

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
        rule = match_rule("GET", "/api/hello")
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
        rule = match_rule("GET", "/api/users/42")
        assert rule is not None
        rule2 = match_rule("GET", "/api/users/abc/def")
        assert rule2 is not None
    finally:
        Path(path).unlink()


def test_match_method_filter():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        rule = match_rule("POST", "/api/echo")
        assert rule is not None
        rule2 = match_rule("GET", "/api/echo")
        assert rule2 is None
    finally:
        Path(path).unlink()


def test_no_match():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        assert match_rule("DELETE", "/api/nonexistent") is None
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
