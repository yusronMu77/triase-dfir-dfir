"""Data uji dari contoh kontrak (kartu svc-backup) dan ingest tiruan lewat `httpx.MockTransport`."""

from __future__ import annotations

import os
from typing import Any

import httpx
import orjson
from astria_common.auth import JwksSource, ServiceTokenVerifier, SigningKey, issue_service_token
from astria_common.events import decode, encode, new_event
from astria_contracts import example
from astria_contracts.events.card_skeleton_created import CardSkeletonCreated, Kode
from sqlalchemy.ext.asyncio import AsyncEngine

from astria_dfir import cases
from astria_dfir.evidence import IngestEvidence

EV1, EV2 = "EV-hb01-004217", "EV-hb01-004231"
CARD = "IC-20261110-003"
DB_URL = os.environ.get("ASTRIA_TEST_DATABASE_URL")


def kode() -> Kode:
    return CardSkeletonCreated.model_validate(example("card.skeleton.created")["data"]).kode


def sha(ev: str) -> str:
    return next(b.sha256 for b in kode().bukti if b.id.root == ev)


def item(ev: str, occurred_at: str, **overrides: Any) -> dict[str, Any]:
    """Satu item `evidence:batch` seperti jawaban ingest."""
    return {
        "evidence_id": ev,
        "source_id": "SRC-hb01",
        "record_no": int(ev.rsplit("-", 1)[1]),
        "record_sha256": sha(ev) if ev in (EV1, EV2) else "0" * 64,
        "occurred_at": occurred_at,
        "host": "WS-ADM-04",
        "account": "svc-backup",
        "kind": "hayabusa.4625",
        "fields": {"TargetUserName": "svc-backup"},
        "excerpt": f"Logon gagal svc-backup ({ev})",
        "sanitized": True,
        "injection_suspect": False,
    } | overrides


def bukti_asli() -> list[dict[str, Any]]:
    # EV2 lebih awal dari EV1: urutan linimasa mengikuti waktu, bukan ID.
    return [item(EV1, "2026-11-10T02:12:00Z"), item(EV2, "2026-11-10T09:05:30+07:00")]


class FakeTokens:
    async def auth_headers(self, audience: str, act: dict[str, Any] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer palsu-{audience}"}


def ingest(items: list[dict[str, Any]] | None = None, *, status: int = 200) -> tuple[IngestEvidence, list[dict]]:
    """IngestEvidence dengan transport tiruan; mengembalikan juga daftar permintaan yang diterima."""
    tersedia = {i["evidence_id"]: i for i in (bukti_asli() if items is None else items)}
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = orjson.loads(request.content)
        calls.append({"path": request.url.path, "auth": request.headers.get("authorization"), "ids": body["ids"]})
        if status != 200:
            return httpx.Response(status, json={"error": {"code": "X", "message": "x", "trace_id": None}})
        found = [tersedia[i] for i in body["ids"] if i in tersedia]
        return httpx.Response(200, json={"items": found, "not_found": [i for i in body["ids"] if i not in tersedia]})

    http = httpx.AsyncClient(base_url="http://ingest.test", transport=httpx.MockTransport(handler))
    return IngestEvidence(FakeTokens(), "http://ingest.test", http=http), calls  # type: ignore[arg-type]


def verifier_for(key: SigningKey) -> ServiceTokenVerifier:
    return ServiceTokenVerifier(JwksSource("http://identity.invalid", fetch=lambda: {"keys": [key.public_jwk()]}))


def auth(key: SigningKey, subject: str = "svc:api", audience: str = "svc:dfir") -> dict[str, str]:
    """Token layanan seperti yang dibawa api: klaim `act` berisi pengguna pemicu."""
    act = {"sub": "usr_jaga01", "roles": ["dfir"]}
    token, _ = issue_service_token(key, subject=subject, audience=audience, act=act)
    return {"Authorization": f"Bearer {token}"}


async def simpan_kartu(engine: AsyncEngine, version: int = 1, **kode: Any) -> None:
    """Kirim `card.skeleton.created` (contoh kontrak, `kode` bisa ditimpa) ke konsumen kartu."""
    data = example("card.skeleton.created")["data"]
    data["kode"] |= kode
    envelope, model = decode(
        encode(new_event("card.skeleton.created", data, correlation={"card_id": CARD, "version": version}))
    )
    async with engine.begin() as conn:
        await cases.on_kartu(conn, envelope, model)
