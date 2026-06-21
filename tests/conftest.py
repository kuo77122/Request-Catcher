import pytest
from typing import Generator
from fastapi.testclient import TestClient

from app.main import app
from app.storage import _request_store, _counter
from app.config import _rules


@pytest.fixture(autouse=True)
def cleanup():
    _request_store.clear()
    _rules.clear()
    yield


@pytest.fixture
def client() -> Generator:
    with TestClient(app) as c:
        yield c
