"""Sifat linimasa: urut (waktu UTC, ID), tanpa duplikat, dan tidak bergantung urutan masukan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from helpers import item
from hypothesis import given
from hypothesis import strategies as st

from astria_dfir.timeline import bangun_linimasa, hash_linimasa

CASE = "CS-20261110-001"
AWAL = datetime(2026, 11, 10, tzinfo=UTC)


@st.composite
def bukti(draw: st.DrawFn) -> list[dict]:
    ids = draw(st.lists(st.integers(1, 999_999), min_size=0, max_size=25, unique=True))
    out = []
    for n in ids:
        detik = draw(st.integers(0, 3600))
        zona = timezone(timedelta(hours=draw(st.sampled_from([0, 7, -5]))))
        out.append(item(f"EV-hb01-{n:06d}", (AWAL + timedelta(seconds=detik)).astimezone(zona).isoformat()))
    # Duplikat identik seperti ID yang dikirim dua kali.
    return out + draw(st.lists(st.sampled_from(out), max_size=5)) if out else out


@given(bukti(), st.randoms())
def test_order_is_deterministic_and_deduplicated(data: list[dict], rnd) -> None:
    acak = list(data)
    rnd.shuffle(acak)
    hasil = bangun_linimasa(CASE, data)
    assert hasil == bangun_linimasa(CASE, acak)
    assert hash_linimasa(hasil) == hash_linimasa(bangun_linimasa(CASE, acak))

    kunci = [(i.ts_utc, i.evidence_id) for i in hasil]
    assert kunci == sorted(kunci)
    assert sorted(i.evidence_id for i in hasil) == sorted({d["evidence_id"] for d in data})
    assert [i.item_id for i in hasil] == [f"TL-20261110-001-{n:05d}" for n in range(1, len(hasil) + 1)]
    assert all(i.ts_utc.tzinfo == UTC for i in hasil)


def test_same_instant_in_different_zones_sorts_by_id() -> None:
    hasil = bangun_linimasa(
        CASE, [item("EV-hb01-000002", "2026-11-10T09:00:00+07:00"), item("EV-hb01-000001", "2026-11-10T02:00:00Z")]
    )
    assert [i.evidence_id for i in hasil] == ["EV-hb01-000001", "EV-hb01-000002"]
