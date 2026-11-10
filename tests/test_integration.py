"""Uji integrasi dengan PostgreSQL sungguhan (`ASTRIA_TEST_DATABASE_URL`, tersedia di kontainer py devenv).

Ingest dipalsukan dengan `httpx.MockTransport` (helpers.ingest); tidak ada panggilan jaringan keluar.
"""

from __future__ import annotations

import uuid

import orjson
import pytest
from astria_common import outbox
from astria_common.db import Database
from astria_common.errors import AstriaError
from astria_common.events import decode
from helpers import CARD, DB_URL, EV1, EV2, bukti_asli, ingest, item, simpan_kartu
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from astria_dfir import cases
from astria_dfir.custody import rantai_utuh, sha256_hex

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not DB_URL, reason="ASTRIA_TEST_DATABASE_URL tidak diisi")]


@pytest.fixture
async def db():
    schema = f"t_{uuid.uuid4().hex[:8]}"
    database = Database(
        SecretStr(DB_URL or ""), schema=schema, metadata=[outbox.metadata, cases.metadata], setup_sql=cases.SETUP_SQL
    )
    await database.start()
    await database.wait_ready(10)
    yield database
    async with database.engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await database.stop()


async def _kartu(db: Database, version: int = 1, **kode) -> None:
    await simpan_kartu(db.engine, version, **kode)


async def _buat(db: Database) -> tuple[dict, bool]:
    async with db.engine.begin() as conn:
        row, baru = await cases.buat_kasus(conn, CARD, "usr_jaga01")
    return dict(row), baru


async def _kasus(db: Database, case_id: str) -> dict:
    async with db.engine.connect() as conn:
        return dict(await cases.kasus(conn, case_id) or {})


async def _custody(db: Database, case_id: str):
    async with db.engine.connect() as conn:
        return await cases.baca_custody(conn, case_id)


async def _outbox(db: Database) -> list:
    async with db.engine.connect() as conn:
        rows = (await conn.execute(select(outbox.outbox.c.envelope).order_by(outbox.outbox.c.created_at))).all()
    return [decode(orjson.dumps(r.envelope)) for r in rows]


async def test_card_to_case_builds_timeline_custody_and_report(db: Database) -> None:
    await _kartu(db)
    row, baru = await _buat(db)
    assert baru and row["status"] == "diminta"
    case_id = row["case_id"]
    assert case_id.startswith("CS-") and case_id.endswith("-001")

    evidence, calls = ingest()
    await cases.bangun_kasus(db.engine, evidence, case_id)

    assert calls[0]["ids"] == [EV1, EV2]
    kasus = await _kasus(db, case_id)
    assert kasus["status"] == "draf_sebagian"
    async with db.engine.connect() as conn:
        items = await cases.linimasa_kasus(conn, case_id, None, 100)
        laporan = await cases.laporan_terbaru(conn, case_id)
    assert [i["evidence_id"] for i in items] == [EV2, EV1]  # urut waktu, bukan ID
    assert (kasus["window_start"], kasus["window_end"]) == (items[0]["ts_utc"], items[-1]["ts_utc"])

    entri = await _custody(db, case_id)
    assert rantai_utuh(entri, case_id)
    assert [e.action for e in entri] == [
        "KASUS_DIBUAT",
        "STATUS",
        "HASH_COCOK",
        "HASH_COCOK",
        "LINIMASA_DIBUAT",
        "STATUS",
        "TULIS_LAPORAN",
        "STATUS",
    ]
    assert entri[0].actor == "usr_jaga01"
    assert [e.detail for e in entri if e.action == "STATUS"] == [
        "diminta>mengumpulkan",
        "mengumpulkan>linimasa_siap",
        "linimasa_siap>draf_sebagian",
    ]

    assert laporan is not None and laporan["html_sha256"] == sha256_hex(laporan["html"])
    assert entri[6].sha256 == laporan["html_sha256"]
    assert "Dugaan pengambilalihan akun layanan svc-backup" in laporan["html"]

    published = await _outbox(db)
    assert [(e.type, d.action) for e, d in published] == [
        ("audit.record", "case.create"),
        ("audit.record", "case.complete"),
    ]
    assert published[1][0].correlation.case_id == case_id
    assert published[1][1].actor == "svc:dfir"


async def test_repeated_request_reuses_case_and_latest_card_version_wins(db: Database) -> None:
    await _kartu(db, version=1)
    pertama, _ = await _buat(db)
    lagi, baru = await _buat(db)
    assert (lagi["case_id"], baru) == (pertama["case_id"], False)

    await _kartu(db, version=2, judul="Versi kedua")
    kedua, baru = await _buat(db)
    assert baru and kedua["card_version"] == 2 and kedua["case_id"].endswith("-002")


async def test_unknown_card_is_404(db: Database) -> None:
    with pytest.raises(AstriaError) as err:
        await _buat(db)
    assert err.value.status == 404


async def test_hash_mismatch_fails_case_without_report(db: Database) -> None:
    await _kartu(db)
    row, _ = await _buat(db)
    evidence, _ = ingest([item(EV1, "2026-11-10T02:12:00Z", record_sha256="f" * 64), bukti_asli()[1]])
    await cases.bangun_kasus(db.engine, evidence, row["case_id"])

    kasus = await _kasus(db, row["case_id"])
    assert kasus["status"] == "gagal"
    assert EV1 in kasus["keterbatasan"][0]
    entri = await _custody(db, row["case_id"])
    assert rantai_utuh(entri, row["case_id"])
    salah = next(e for e in entri if e.action == "HASH_TIDAK_COCOK")
    assert (salah.artifact_id, salah.sha256) == (EV1, "f" * 64)
    assert entri[-1].detail == "mengumpulkan>gagal"
    async with db.engine.connect() as conn:
        assert await cases.laporan_terbaru(conn, row["case_id"]) is None
        assert await cases.linimasa_kasus(conn, row["case_id"], None, 10) == []

    # Kasus gagal boleh diminta ulang: kasus baru untuk versi kartu yang sama.
    ulang, baru = await _buat(db)
    assert baru and ulang["case_id"] != row["case_id"]


async def test_missing_evidence_is_a_limitation_not_a_failure(db: Database) -> None:
    await _kartu(db)
    row, _ = await _buat(db)
    evidence, _ = ingest([bukti_asli()[0]])
    await cases.bangun_kasus(db.engine, evidence, row["case_id"])

    kasus = await _kasus(db, row["case_id"])
    assert kasus["status"] == "draf_sebagian"
    assert any(EV2 in k for k in kasus["keterbatasan"])
    assert "BUKTI_TIDAK_DITEMUKAN" in [e.action for e in await _custody(db, row["case_id"])]


async def test_ingest_unavailable_fails_case(db: Database) -> None:
    await _kartu(db)
    row, _ = await _buat(db)
    evidence, _ = ingest(status=503)
    await cases.bangun_kasus(db.engine, evidence, row["case_id"])

    kasus = await _kasus(db, row["case_id"])
    assert kasus["status"] == "gagal"
    assert "HTTPStatusError" in kasus["keterbatasan"][0]
    assert rantai_utuh(await _custody(db, row["case_id"]), row["case_id"])


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE custody_event SET actor = 'penyusup'",
        "DELETE FROM custody_event",
        "TRUNCATE custody_event",
    ],
)
async def test_custody_is_append_only_in_postgres(db: Database, statement: str) -> None:
    await _kartu(db)
    row, _ = await _buat(db)
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.engine.begin() as conn:
            await conn.execute(text(statement))
    entri = await _custody(db, row["case_id"])
    assert len(entri) == 1 and entri[0].actor == "usr_jaga01"
