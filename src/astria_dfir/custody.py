"""Rantai penguasaan bukti (custody) berantai hash, kode murni (note app-dfir 5.4).

`entry_hash = sha256(prev_hash + json_kanonik(isi))`; entri pertama memakai `sha256("dfir-genesis:" + case_id)`.
Semua nilai isi berupa teks, bilangan bulat, atau None, jadi `orjson` dengan kunci terurut sudah deterministik.
Mengubah, menghapus, atau menyisipkan satu entri memutus rantai.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import orjson


def sha256_hex(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def genesis(case_id: str) -> str:
    return sha256_hex(f"dfir-genesis:{case_id}")


@dataclass(frozen=True)
class Isi:
    case_id: str
    seq: int
    ts: str
    """Waktu UTC ISO-8601 dengan mikrodetik dan akhiran Z; bentuk teks ini yang di-hash."""
    actor: str
    action: str
    artifact_id: str | None = None
    sha256: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Entri(Isi):
    prev_hash: str = ""
    entry_hash: str = ""


def _hash(prev: str, isi: Isi) -> str:
    return sha256_hex(prev + orjson.dumps(asdict(isi), option=orjson.OPT_SORT_KEYS).decode("utf-8"))


def sambung(prev: str | None, isi: Isi) -> Entri:
    prev = prev or genesis(isi.case_id)
    return Entri(**asdict(isi), prev_hash=prev, entry_hash=_hash(prev, isi))


def rantai_utuh(entri: Sequence[Entri], case_id: str) -> bool:
    prev = genesis(case_id)
    for n, e in enumerate(entri, start=1):
        isi = Isi(e.case_id, e.seq, e.ts, e.actor, e.action, e.artifact_id, e.sha256, e.detail)
        if e.case_id != case_id or e.seq != n or e.prev_hash != prev or _hash(prev, isi) != e.entry_hash:
            return False
        prev = e.entry_hash
    return True
