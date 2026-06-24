import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.config import load_config


SAMPLE_YAML = """
rules:
  - path: /api/hello
    method: GET
    status_code: 200
    headers:
      Content-Type: application/json
    body: '{"msg":"hello world"}'

  - path: /api/error
    method: GET
    status_code: 500
    body: '{"error":"internal"}'

  - path: /api/echo
    method: POST
    status_code: 201
    body: '{"echo":true}'
"""


def test_frontend(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Request Catcher" in resp.text


def test_list_requests_empty(client: TestClient):
    resp = client.get("/__requests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_capture_request(client: TestClient):
    resp = client.post("/some/path?foo=bar", json={"key": "val"}, headers={"X-Custom": "abc"})
    assert resp.status_code == 200

    resp2 = client.get("/__requests")
    data = resp2.json()
    assert len(data) == 1
    assert data[0]["method"] == "POST"
    assert data[0]["path"] == "/some/path"
    assert data[0]["query_params"] == {"foo": "bar"}
    assert data[0]["headers"]["x-custom"] == "abc"
    assert json.loads(data[0]["body"]) == {"key": "val"}


def test_clear_requests(client: TestClient):
    client.post("/test")
    assert len(client.get("/__requests").json()) == 1
    resp = client.delete("/__requests")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert client.get("/__requests").json() == []


def test_get_request_by_id(client: TestClient):
    client.get("/abc")
    rid = client.get("/__requests").json()[0]["id"]
    resp = client.get(f"/__requests/{rid}")
    assert resp.status_code == 200
    assert resp.json()["path"] == "/abc"


def test_get_request_by_id_not_found(client: TestClient):
    resp = client.get("/__requests/999")
    assert resp.status_code == 404


def test_get_config(client: TestClient):
    resp = client.get("/__config")
    assert resp.status_code == 200
    assert "rules" in resp.json()


def test_reload_config(client: TestClient):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        app.dependency_overrides = {}
        load_config(path)
        resp = client.get("/__config")
        assert len(resp.json()["rules"]) == 3
    finally:
        Path(path).unlink()


def test_rule_intercepts_get(client: TestClient):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        resp = client.get("/api/hello")
        assert resp.status_code == 200
        assert resp.json() == {"msg": "hello world"}
    finally:
        Path(path).unlink()


def test_rule_intercepts_post(client: TestClient):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        resp = client.post("/api/echo", json={"test": 1})
        assert resp.status_code == 201
        assert resp.json() == {"echo": True}
    finally:
        Path(path).unlink()


def test_rule_with_error_status(client: TestClient):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = f.name
    try:
        load_config(path)
        resp = client.get("/api/error")
        assert resp.status_code == 500
        assert resp.json() == {"error": "internal"}
    finally:
        Path(path).unlink()


def test_captured_request_has_detail(client: TestClient):
    client.put("/data", content=b"raw body", headers={"Content-Type": "text/plain"})
    data = client.get("/__requests").json()
    assert len(data) == 1
    r = data[0]
    assert r["method"] == "PUT"
    assert r["body"] == "raw body"
    assert r["client_host"] is not None


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


def test_reload_bad_config_returns_400(client: TestClient):
    valid_yaml = """
rules:
  - path: /api/x
    method: GET
    body: '{}'
"""
    path = _load_yaml_in_main(valid_yaml)
    try:
        resp = client.get("/api/x")
        assert resp.status_code == 200

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
