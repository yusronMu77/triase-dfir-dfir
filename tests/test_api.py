"""Endpoint tanpa basis data: token layanan, daftar pemanggil, dan validasi masukan."""

from __future__ import annotations

from typing import Any

import pytest
from astria_common.auth import SigningKey
from fastapi.testclient import TestClient
from helpers import auth, verifier_for

from astria_dfir.main import build_app
from astria_dfir.settings import Settings

CASE = "CS-20261110-001"
ENDPOINTS = [
    ("post", "/internal/v1/cases", {"card_id": "IC-20261110-003"}),
    ("get", f"/internal/v1/cases/{CASE}", None),
    ("get", f"/internal/v1/cases/{CASE}/timeline", None),
    ("get", f"/internal/v1/cases/{CASE}/custody", None),
    ("get", f"/internal/v1/cases/{CASE}/report?format=html", None),
]


@pytest.fixture
def client(key: SigningKey):
    with TestClient(build_app(Settings(env="test"), token_verifier=verifier_for(key))) as c:
        yield c


def _call(client: TestClient, method: str, path: str, body: Any, headers: dict[str, str] | None = None):
    return client.request(method.upper(), path, json=body, headers=headers or {})


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_token_required(client: TestClient, method: str, path: str, body: Any) -> None:
    resp = _call(client, method, path, body)
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "TOKEN_WAJIB")


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_wrong_audience_is_rejected(client: TestClient, key: SigningKey, method: str, path: str, body: Any) -> None:
    resp = _call(client, method, path, body, auth(key, audience="svc:ingest"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "AUDIENS_SALAH")


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize("subject", ["svc:triage", "svc:gateway", "svc:verifier"])
def test_only_api_may_call(
    client: TestClient, key: SigningKey, method: str, path: str, body: Any, subject: str
) -> None:
    resp = _call(client, method, path, body, auth(key, subject))
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "PEMANGGIL_DITOLAK")


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_api_passes_auth_and_gets_503_without_database(
    client: TestClient, key: SigningKey, method: str, path: str, body: Any
) -> None:
    resp = _call(client, method, path, body, auth(key))
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "DFIR_TIDAK_SIAP")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/internal/v1/cases", {"card_id": "bukan-kartu"}),
        ("post", "/internal/v1/cases", {"card_id": "IC-20261110-003", "lain": 1}),
        ("get", "/internal/v1/cases/CS-1", None),
        ("get", f"/internal/v1/cases/{CASE}/report?format=pdf", None),
        ("get", f"/internal/v1/cases/{CASE}/timeline?limit=0", None),
        ("get", f"/internal/v1/cases/{CASE}/timeline?cursor=x", None),
    ],
)
def test_invalid_input_is_rejected(client: TestClient, key: SigningKey, method: str, path: str, body: Any) -> None:
    resp = _call(client, method, path, body, auth(key))
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "VALIDASI_GAGAL")
