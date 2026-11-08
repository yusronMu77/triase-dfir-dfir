"""Jalankan layanan: `astria-dfir` atau `python -m astria_dfir`."""

from astria_common.app import run

from astria_dfir.main import build_app
from astria_dfir.settings import Settings


def main() -> None:
    settings = Settings()
    run(build_app(settings), settings)


if __name__ == "__main__":
    main()
