from collections import deque
from app.models import StoredRequest

MAX_REQUESTS = 100

_request_store: deque[StoredRequest] = deque(maxlen=MAX_REQUESTS)
_counter = 0


def store_request(request: StoredRequest) -> None:
    global _counter
    _counter += 1
    request.id = _counter
    _request_store.appendleft(request)


def get_requests() -> list[StoredRequest]:
    return list(_request_store)


def get_request_by_id(request_id: int) -> StoredRequest | None:
    for r in _request_store:
        if r.id == request_id:
            return r
    return None


def clear_requests() -> None:
    _request_store.clear()
