import os

import pytest
from astria_common.auth import SigningKey, load_or_create_signing_key


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_astria_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uji tidak boleh membaca ASTRIA_* milik kontainer dev."""
    for key in list(os.environ):
        if key.startswith("ASTRIA_") and not key.startswith("ASTRIA_TEST_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def key(tmp_path_factory: pytest.TempPathFactory) -> SigningKey:
    """Kunci identity uji untuk menerbitkan token layanan."""
    return load_or_create_signing_key(tmp_path_factory.mktemp("kunci") / "identity.pem")
