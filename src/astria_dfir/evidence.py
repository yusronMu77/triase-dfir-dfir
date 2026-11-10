"""Bukti kartu dari ingest `POST /svc/v1/evidence:batch` (maks 500 ID per panggilan), berkunci token layanan.

dfir tidak membaca basis data atau volume bukti ingest (note app-dfir 1.1). Jawaban dikembalikan apa adanya:
`{items: [...], not_found: [...]}`; galat HTTP diteruskan ke pemanggil supaya kasus ditandai gagal.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import httpx
from astria_common.auth import ServiceTokenClient
from astria_common.http import make_client

BATCH_PATH = "/svc/v1/evidence:batch"
MAX_BATCH_IDS = 500


class EvidenceSource(Protocol):
    async def fetch(self, ids: Sequence[str]) -> dict[str, Any]: ...


class IngestEvidence:
    def __init__(self, tokens: ServiceTokenClient, base_url: str, *, http: httpx.AsyncClient | None = None) -> None:
        self._tokens = tokens
        self._http = http or make_client(base_url)

    async def fetch(self, ids: Sequence[str]) -> dict[str, Any]:
        unik = sorted(set(ids))
        hasil: dict[str, Any] = {"items": [], "not_found": []}
        for i in range(0, len(unik), MAX_BATCH_IDS):
            headers = await self._tokens.auth_headers("svc:ingest")
            resp = await self._http.post(BATCH_PATH, json={"ids": unik[i : i + MAX_BATCH_IDS]}, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            hasil["items"] += body["items"]
            hasil["not_found"] += body["not_found"]
        return hasil
