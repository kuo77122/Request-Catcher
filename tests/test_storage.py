from datetime import datetime
from app.storage import store_request, get_requests, get_request_by_id, clear_requests
from app.models import StoredRequest


def _make(method: str = "GET", path: str = "/test") -> StoredRequest:
    return StoredRequest(
        id=0,
        timestamp=datetime.now(),
        method=method,
        path=path,
        headers={"host": "example.com"},
    )


def test_store_and_list():
    r = _make()
    store_request(r)
    stored = get_requests()
    assert len(stored) == 1
    assert stored[0].path == "/test"


def test_store_max_100():
    for i in range(110):
        store_request(_make(path=f"/path-{i}"))
    stored = get_requests()
    assert len(stored) == 100
    assert stored[0].path == "/path-109"
    assert stored[-1].path == "/path-10"


def test_get_by_id():
    store_request(_make())
    stored = get_requests()
    rid = stored[0].id
    found = get_request_by_id(rid)
    assert found is not None
    assert found.id == rid


def test_get_by_id_missing():
    assert get_request_by_id(999) is None


def test_clear():
    store_request(_make())
    assert len(get_requests()) == 1
    clear_requests()
    assert len(get_requests()) == 0
