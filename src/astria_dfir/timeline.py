"""Linimasa deterministik dari bukti kartu, kode murni (note app-dfir 5.3).

Dedup per ID bukti, urut `(occurred_at UTC, evidence_id)`, lalu diberi nomor `TL-<kasus>-<nnnnn>`. Hasilnya sama
untuk masukan yang sama walau urutan masuk berubah. Jendela kasus adalah waktu bukti paling awal sampai paling akhir.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import orjson

from astria_dfir.custody import sha256_hex


@dataclass(frozen=True)
class Item:
    item_id: str
    ts_utc: datetime
    evidence_id: str
    source_id: str
    host: str
    account: str | None
    kind: str
    ringkasan: str
    injection_suspect: bool


def waktu_utc(teks: str) -> datetime:
    ts = datetime.fromisoformat(teks)
    if ts.tzinfo is None:
        raise ValueError(f"waktu tanpa zona: {teks!r}")
    return ts.astimezone(UTC)


def iso_utc(ts: datetime | None) -> str | None:
    """Bentuk teks waktu tunggal (UTC, mikrodetik, akhiran Z); dipakai custody (di-hash) dan API."""
    return ts.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z") if ts is not None else None


def bangun_linimasa(case_id: str, bukti: Iterable[Mapping[str, Any]]) -> list[Item]:
    """`bukti` berbentuk item ingest `evidence:batch`."""
    unik = {b["evidence_id"]: b for b in bukti}
    waktu = {eid: waktu_utc(b["occurred_at"]) for eid, b in unik.items()}
    urut = sorted(unik, key=lambda eid: (waktu[eid], eid))
    pendek = case_id.removeprefix("CS-")
    return [
        Item(
            item_id=f"TL-{pendek}-{n:05d}",
            ts_utc=waktu[eid],
            evidence_id=eid,
            source_id=unik[eid]["source_id"],
            host=unik[eid]["host"],
            account=unik[eid].get("account"),
            kind=unik[eid]["kind"],
            ringkasan=unik[eid].get("excerpt") or "",
            injection_suspect=bool(unik[eid].get("injection_suspect")),
        )
        for n, eid in enumerate(urut, start=1)
    ]


def hash_linimasa(items: Iterable[Item]) -> str:
    """Sidik linimasa untuk entri custody `LINIMASA_DIBUAT`."""
    return sha256_hex(orjson.dumps([asdict(i) for i in items], option=orjson.OPT_SORT_KEYS))
