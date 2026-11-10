"""Konfigurasi dfir dari env `ASTRIA_*`."""

from astria_common.settings import AstriaSettings


class Settings(AstriaSettings):
    service_name: str = "dfir"

    dfir_ingest_url: str = "http://ingest:8000"
    """Basis URL ingest untuk membaca bukti kartu (ASTRIA_DFIR_INGEST_URL), `POST /svc/v1/evidence:batch`.
    Wajib bersama `ASTRIA_SERVICE_KEY`/`_FILE` bila basis data diisi."""
