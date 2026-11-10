"""Alur HTTP ujung ke ujung dengan PostgreSQL sungguhan: kartu, POST kasus, lalu linimasa, custody, dan laporan."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from helpers import CARD, DB_URL, EV1, EV2, auth, ingest, simpan_kartu, verifier_for
from pydantic import SecretStr
from sqlalchemy import text

from astria_dfir.custody import sha256_hex
from astria_dfir.main import build_app
from astria_dfir.settings import Settings

pytestmark = pytest.mark.skipif(not DB_URL, reason="ASTRIA_TEST_DATABASE_URL tidak diisi")


@pytest.fixture
def client(key):
    schema = f"t_{uuid.uuid4().hex[:8]}"
    settings = Settings(env="test", database_url=SecretStr(DB_URL or ""), db_schema=schema)
    evidence, _ = ingest()
    app = build_app(settings, evidence=evidence, token_verifier=verifier_for(key))
    with TestClient(app) as c:
        deadline = time.monotonic() + 10
        while c.get("/ready").status_code != 200 and time.monotonic() < deadline:
            time.sleep(0.1)
        yield c
        c.portal.call(_drop, app.state.components["postgres"].engine, schema)


async def _drop(engine, schema: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_case_flow_over_http(client: TestClient, key) -> None:
    h = auth(key)
    assert client.post("/internal/v1/cases", json={"card_id": CARD}, headers=h).status_code == 404

    client.portal.call(simpan_kartu, client.app.state.components["postgres"].engine)
    resp = client.post("/internal/v1/cases", json={"card_id": CARD}, headers=h)
    assert resp.status_code == 202
    body = resp.json()
    case_id = body["case_id"]
    assert (body["status"], body["card_version"]) == ("diminta", 1)

    # TestClient menjalankan tugas latar sebelum kembali, jadi kasus sudah dibangun.
    kasus = client.get(f"/internal/v1/cases/{case_id}", headers=h).json()
    assert kasus["status"] == "draf_sebagian"
    assert kasus["requested_by"] == "usr_jaga01"
    assert kasus["window"] == {"start": "2026-11-10T02:05:30.000000Z", "end": "2026-11-10T02:12:00.000000Z"}

    halaman = client.get(f"/internal/v1/cases/{case_id}/timeline?limit=1", headers=h).json()
    assert [i["evidence_id"] for i in halaman["items"]] == [EV2]
    lanjut = client.get(f"/internal/v1/cases/{case_id}/timeline?cursor={halaman['next_cursor']}", headers=h).json()
    assert ([i["evidence_id"] for i in lanjut["items"]], lanjut["next_cursor"]) == ([EV1], None)

    custody = client.get(f"/internal/v1/cases/{case_id}/custody", headers=h).json()
    assert custody["chain_ok"] is True
    assert [e["seq"] for e in custody["entries"]] == list(range(1, len(custody["entries"]) + 1))

    laporan = client.get(f"/internal/v1/cases/{case_id}/report?format=html", headers=h)
    assert laporan.status_code == 200
    assert laporan.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in laporan.headers["content-security-policy"]
    assert laporan.headers["x-report-sha256"] == sha256_hex(laporan.text) == kasus["report"]["html_sha256"]

    again = client.post("/internal/v1/cases", json={"card_id": CARD}, headers=h).json()
    assert (again["case_id"], again["status"]) == (case_id, "draf_sebagian")


def test_unknown_case_is_404(client: TestClient, key) -> None:
    resp = client.get("/internal/v1/cases/CS-20261110-999/custody", headers=auth(key))
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "KASUS_TIDAK_DITEMUKAN")
