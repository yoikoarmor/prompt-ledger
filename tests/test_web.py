import base64

import pytest
from conftest import payload
from fastapi.testclient import TestClient

from prompt_ledger.web import create_app


@pytest.fixture
def client(cfg, ledger):
    with TestClient(create_app(cfg, ledger), base_url="http://127.0.0.1:8765") as client:
        yield client


def auth(client):
    return {"X-Ledger-Token": client.get("/session").json()["token"]}


def test_api_shared_core_privacy(client, ledger):
    headers = auth(client)
    files = [{"name": "a.json", "content_base64": base64.b64encode(payload("base")).decode()}]
    result = client.post("/api/import", json={"files": files}, headers=headers)
    assert result.status_code == 200
    assert result.json()["results"][0]["status"] == "accepted"
    assert ledger.status()["counts"]["accepted"] == 1
    assert "base" not in client.get("/api/records", headers=headers).text
    assert "base" in client.get("/api/export", headers=headers).text


def test_security(client):
    assert client.get("/api/status").status_code == 403
    assert client.get("/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/session", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    response = client.get("/")
    assert "img-src 'none'" in response.headers["content-security-policy"]
    assert "https://" not in response.text


def test_request_errors_do_not_echo_data(client):
    headers = auth(client)
    response = client.post("/api/import", json={"files": "private synthetic text"}, headers=headers)
    assert response.status_code == 422
    assert "private synthetic text" not in response.text
    response = client.post(
        "/api/import",
        json={"files": [{"name": "../private.json", "content_base64": "e30="}]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_path"


def test_check_only_and_openapi(client):
    response = client.post(
        "/api/import",
        json={"check_only": True, "files": [{"name": "a.json", "content_base64": "e30="}]},
        headers=auth(client),
    )
    assert response.json()["results"][0]["status"] == "review"
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/import"]["post"]["security"] == [{"LocalSessionToken": []}]


def test_streamed_body_limit(client, monkeypatch):
    from prompt_ledger import web

    monkeypatch.setattr(web, "MAX_REQUEST", 100)
    response = client.post("/api/import", content=b"x" * 101, headers=auth(client))
    assert response.status_code == 413


def test_file_and_batch_limits(client):
    response = client.post(
        "/api/import",
        json={"files": [{"name": "a.json", "content_base64": "e30="}] * 21},
        headers=auth(client),
    )
    assert response.status_code == 422


def test_repeat_review_correction_returns_conflict(client, ledger):
    record_id = ledger.ingest([("review.json", b"{}")])["results"][0]["id"]
    headers = auth(client)
    first = client.post(
        f"/api/review/{record_id}", json={"positive": "base", "negative": ""}, headers=headers
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/review/{record_id}", json={"positive": "opposite", "negative": ""}, headers=headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_resolved"
