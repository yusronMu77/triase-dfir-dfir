"""Draf laporan HTML dari templat Jinja2 dengan autoescape (note app-dfir 5.6).

Semua teks berasal dari kode kartu, linimasa, dan custody; tidak ada narasi model. Teks bukti dari log tetap
di-escape karena bisa memuat HTML atau instruksi sisipan. Waktu disimpan UTC dan ditampilkan WIB.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from astria_contracts.events.card_skeleton_created import Kode
from jinja2 import Environment, PackageLoader, StrictUndefined

from astria_dfir.custody import Entri, rantai_utuh
from astria_dfir.timeline import Item, waktu_utc

TEMPLAT = "laporan-v1"
WIB = timezone(timedelta(hours=7), "WIB")  # Indonesia tanpa DST; tidak bergantung tzdata


def _wib(nilai: datetime | str) -> str:
    ts = waktu_utc(nilai) if isinstance(nilai, str) else nilai
    return ts.astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


_env = Environment(loader=PackageLoader("astria_dfir", "templates"), autoescape=True, undefined=StrictUndefined)
_env.filters["wib"] = _wib


def render(
    *,
    kasus: Mapping[str, Any],
    kode: Kode,
    items: Sequence[Item],
    custody: Sequence[Entri],
    keterbatasan: Sequence[str],
    lampiran: Sequence[Mapping[str, Any]],
    dibuat: datetime,
) -> str:
    return _env.get_template(f"{TEMPLAT}.html").render(
        kasus=kasus,
        kode=kode,
        items=items,
        custody=custody,
        rantai_ok=rantai_utuh(custody, kasus["case_id"]),
        keterbatasan=keterbatasan,
        lampiran=lampiran,
        dibuat=dibuat,
        templat=TEMPLAT,
    )
