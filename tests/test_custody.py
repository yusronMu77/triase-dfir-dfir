"""Sifat rantai custody: rantai yang dibangun selalu utuh; perubahan apa pun pada satu entri memutusnya."""

from __future__ import annotations

import dataclasses

from hypothesis import given
from hypothesis import strategies as st

from astria_dfir.custody import Entri, Isi, genesis, rantai_utuh, sambung, sha256_hex

CASE = "CS-20261110-001"
teks = st.text(min_size=1, max_size=20)
opsional = st.none() | teks
isi_entri = st.tuples(teks, teks, opsional, opsional, opsional)


def rantai(spesifikasi: list[tuple]) -> list[Entri]:
    out: list[Entri] = []
    for n, (actor, action, artefak, sha, detail) in enumerate(spesifikasi, start=1):
        isi = Isi(CASE, n, f"2026-11-10T02:00:{n % 60:02d}.000000Z", actor, action, artefak, sha, detail)
        out.append(sambung(out[-1].entry_hash if out else None, isi))
    return out


def test_first_entry_links_to_genesis() -> None:
    e = rantai([("svc:dfir", "KASUS_DIBUAT", None, None, None)])[0]
    assert e.prev_hash == genesis(CASE) == sha256_hex(f"dfir-genesis:{CASE}")


@given(st.lists(isi_entri, max_size=8))
def test_built_chain_is_intact(spesifikasi: list[tuple]) -> None:
    assert rantai_utuh(rantai(spesifikasi), CASE)


FIELDS = ["case_id", "seq", "ts", "actor", "action", "artifact_id", "sha256", "detail", "prev_hash", "entry_hash"]


@given(st.lists(isi_entri, min_size=1, max_size=8), st.data())
def test_tampering_any_field_of_any_entry_breaks_chain(spesifikasi: list[tuple], data: st.DataObject) -> None:
    entri = rantai(spesifikasi)
    idx = data.draw(st.integers(0, len(entri) - 1))
    field = data.draw(st.sampled_from(FIELDS))
    lama = getattr(entri[idx], field)
    baru = data.draw((st.integers(0, 10**6) if field == "seq" else opsional).filter(lambda v: v != lama))
    entri[idx] = dataclasses.replace(entri[idx], **{field: baru})
    assert not rantai_utuh(entri, CASE)


@given(st.lists(isi_entri, min_size=2, max_size=8), st.data())
def test_deleting_or_swapping_entries_breaks_chain(spesifikasi: list[tuple], data: st.DataObject) -> None:
    entri = rantai(spesifikasi)
    i = data.draw(st.integers(0, len(entri) - 2))
    hapus = entri[:i] + entri[i + 1 :]
    tukar = entri[:i] + [entri[i + 1], entri[i]] + entri[i + 2 :]
    assert not rantai_utuh(hapus, CASE)
    assert not rantai_utuh(tukar, CASE)


def test_chain_of_other_case_is_rejected() -> None:
    assert not rantai_utuh(rantai([("svc:dfir", "STATUS", None, None, "a>b")]), "CS-20261110-002")
