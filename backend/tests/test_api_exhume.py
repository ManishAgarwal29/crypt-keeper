"""Endpoint tests for POST /exhume that need no network (validation fails before any request)."""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_exhume_rejects_invalid_pypi_name_with_422():
    r = client.post("/exhume", json={"ref": {"source": "pypi", "identifier": "../bad"}})
    assert r.status_code == 422
    assert "valid PyPI package name" in r.json()["detail"]


def test_exhume_rejects_non_github_url_with_422():
    r = client.post("/exhume", json={"ref": {"source": "github", "identifier": "https://gitlab.com/o/r"}})
    assert r.status_code == 422
    assert "GitHub" in r.json()["detail"]
