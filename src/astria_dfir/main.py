"""Rakit aplikasi dfir: komponen standar, konsumen kartu, klien bukti ingest, dan router.

Dev memakai `uvicorn astria_dfir.main:build_app --factory --reload`; image memakai `astria-dfir`.
"""

from astria_common.app import create_app
from astria_common.auth import ServiceTokenClient, ServiceTokenVerifier
from astria_common.consumer import Consumer
from astria_common.service import standard_runtime
from fastapi import FastAPI

from astria_dfir import api
from astria_dfir.cases import SETUP_SQL, metadata, on_kartu
from astria_dfir.evidence import EvidenceSource, IngestEvidence
from astria_dfir.settings import Settings


def _evidence(settings: Settings) -> IngestEvidence:
    key = settings.service_key_bytes()
    if key is None:
        raise RuntimeError("dfir: ASTRIA_SERVICE_KEY atau _FILE wajib untuk membaca bukti dari ingest")
    tokens = ServiceTokenClient(settings.service_name, key, settings.identity_url)
    return IngestEvidence(tokens, settings.dfir_ingest_url)


def build_app(
    settings: Settings | None = None,
    *,
    evidence: EvidenceSource | None = None,
    token_verifier: ServiceTokenVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings()
    runtime = standard_runtime(settings, metadata=[metadata], setup_sql=SETUP_SQL)
    if runtime.db is not None and evidence is None:
        evidence = _evidence(settings)
    if runtime.db is not None and runtime.bus is not None:
        runtime.add(
            Consumer(
                runtime.bus, runtime.db, durable="dfir-kartu", event_type="card.skeleton.created", handler=on_kartu
            )
        )
    app = create_app(settings, components=runtime.components, routers=[api.router], token_verifier=token_verifier)
    app.state.evidence = evidence
    return app
