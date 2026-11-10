import pytest
from astria_common.envguard import EnvironmentGuardError
from fastapi.testclient import TestClient
from pydantic import SecretStr

from astria_dfir.main import build_app
from astria_dfir.settings import Settings


def test_health_and_ready_without_dependencies() -> None:
    with TestClient(build_app(Settings(env="test"))) as client:
        health = client.get("/health").json()
        assert health["service"] == "dfir"
        assert health["env"] == "test"
        assert client.get("/ready").status_code == 200
        assert "astria_service_info" in client.get("/metrics").text


def test_refuses_to_start_with_non_dev_database_in_dev() -> None:
    url = SecretStr("postgresql+psycopg://u:p@postgres:5432/astria")
    with pytest.raises(EnvironmentGuardError):
        build_app(Settings(env="dev", database_url=url, service_key=SecretStr("ab" * 32)))


DEV_DB = SecretStr("postgresql+psycopg://u:p@postgres:5432/dfir_dev")


def _dev(**overrides) -> Settings:
    """Dibuat di dalam uji, sesudah conftest membersihkan env ASTRIA_* kontainer dev."""
    values = {"env": "dev", "database_url": DEV_DB, "nats_url": "nats://nats:4222", "service_key": SecretStr("ab" * 32)}
    return Settings(**values | overrides)


def test_card_consumer_is_wired_when_db_and_nats_configured() -> None:
    assert "consumer:dfir-kartu" in build_app(_dev()).state.components


def test_service_key_is_required_to_read_evidence_from_ingest() -> None:
    with pytest.raises(RuntimeError):
        build_app(_dev(service_key=None))
