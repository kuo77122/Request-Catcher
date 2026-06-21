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
