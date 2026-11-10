"""Kasus DFIR minimal: kartu, kasus, linimasa, custody, dan draf laporan HTML (note app-dfir 4, 5.2 sampai 5.6).

Kode kartu disimpan dari `card.skeleton.created` (versi terbaru menang), karena api hanya mengirim `card_id`.
`buat_kasus` mencatat kasus `diminta`; `bangun_kasus` membaca bukti kartu dari ingest, mencocokkan hash dengan
kartu, lalu menulis linimasa, custody, dan laporan dalam satu transaksi. Hash tidak cocok berarti kasus `gagal`
tanpa laporan (5.5). Setiap perubahan status menjadi entri custody `STATUS`. `custody_event` append-only: kode
hanya menyisipkan, dan pemicu PostgreSQL menolak UPDATE, DELETE, dan TRUNCATE (4.5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from astria_common.audit_client import audit_event
from astria_common.errors import AstriaError
from astria_common.log import get_logger
from astria_common.outbox import enqueue
from astria_contracts import Envelope
from astria_contracts.events.card_skeleton_created import CardSkeletonCreated, Kode
from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from astria_dfir import report
from astria_dfir.custody import Entri, Isi, sambung, sha256_hex
from astria_dfir.evidence import EvidenceSource
from astria_dfir.timeline import bangun_linimasa, hash_linimasa, iso_utc, waktu_utc

log = get_logger(__name__)

AKTOR = "svc:dfir"
CASE_ID_PATTERN = r"^CS-[0-9]{8}-[0-9]{3,}$"
# Transisi yang dipakai versi minimal (subset note app-dfir 4.2). Tanpa narasi, draf berakhir `draf_sebagian`
# karena semua bagian memakai templat tetap.
ALUR = {
    ("diminta", "mengumpulkan"),
    ("mengumpulkan", "linimasa_siap"),
    ("mengumpulkan", "gagal"),
    ("linimasa_siap", "draf_sebagian"),
}
KETERBATASAN_TETAP = (
    "Linimasa hanya memuat event ter-normalisasi ingest (D2) untuk bukti yang dirujuk kartu; artefak mentah dan "
    "event lain di host yang sama tidak dibaca.",
    "Ringkasan disusun dari kode kartu tanpa narasi model; bagian IOC, teknik ATT&CK, dan dampak belum dibuat.",
)

metadata = MetaData()

kartu = Table(
    "kartu",
    metadata,
    Column("card_id", Text, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("kode", JSONB, nullable=False),
    Column("diterima_pada", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
case_file = Table(
    "case_file",
    metadata,
    Column("case_id", Text, primary_key=True),
    Column("card_id", Text, nullable=False, index=True),
    Column("card_version", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("requested_by", Text, nullable=False),
    Column("window_start", DateTime(timezone=True)),
    Column("window_end", DateTime(timezone=True)),
    Column("keterbatasan", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
timeline_item = Table(
    "timeline_item",
    metadata,
    Column("item_id", Text, primary_key=True),
    Column("case_id", Text, ForeignKey("case_file.case_id"), nullable=False),
    Column("ts_utc", DateTime(timezone=True), nullable=False),
    Column("evidence_id", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("host", Text, nullable=False),
    Column("account", Text),
    Column("kind", Text, nullable=False),
    Column("ringkasan", Text, nullable=False),
    Column("injection_suspect", Boolean, nullable=False),
    Index("timeline_item_urut", "case_id", "ts_utc", "evidence_id"),
)
custody_event = Table(
    "custody_event",
    metadata,
    Column("case_id", Text, ForeignKey("case_file.case_id"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("artifact_id", Text),
    Column("sha256", Text),
    Column("detail", Text),
    Column("prev_hash", Text, nullable=False),
    Column("entry_hash", Text, nullable=False),
)
report_version = Table(
    "report_version",
    metadata,
    Column("case_id", Text, ForeignKey("case_file.case_id"), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("template_version", Text, nullable=False),
    Column("html", Text, nullable=False),
    Column("html_sha256", Text, nullable=False),
    Column("overall_verdict", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Tahap 0 belum memakai Alembic: dijalankan tiap start, jadi harus idempoten (CREATE OR REPLACE).
SETUP_SQL = (
    """CREATE OR REPLACE FUNCTION custody_tolak_ubah() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'custody_event bersifat append-only';
END;
$$""",
    "CREATE OR REPLACE TRIGGER custody_tanpa_ubah BEFORE UPDATE OR DELETE ON custody_event "
    "FOR EACH ROW EXECUTE FUNCTION custody_tolak_ubah()",
    "CREATE OR REPLACE TRIGGER custody_tanpa_truncate BEFORE TRUNCATE ON custody_event "
    "FOR EACH STATEMENT EXECUTE FUNCTION custody_tolak_ubah()",
)


async def _kunci(conn: AsyncConnection, kunci: str) -> None:
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": kunci})


# ---- kartu ----


async def on_kartu(conn: AsyncConnection, envelope: Envelope, data: BaseModel) -> None:
    """Konsumen `card.skeleton.created`: simpan kode kartu per (card_id, version)."""
    assert isinstance(data, CardSkeletonCreated)
    card_id, version = envelope.correlation.card_id, envelope.correlation.version
    if card_id is None or version is None:
        raise ValueError("card.skeleton.created tanpa correlation card_id/version")
    await conn.execute(
        insert(kartu)
        .values(card_id=card_id, version=version, kode=data.kode.model_dump(mode="json"))
        .on_conflict_do_nothing()
    )
    log.info("kartu_disimpan", card_id=card_id, version=version)


# ---- custody ----


async def tambah_custody(
    conn: AsyncConnection,
    case_id: str,
    actor: str,
    action: str,
    *,
    artifact_id: str | None = None,
    sha256: str | None = None,
    detail: str | None = None,
) -> Entri:
    """Satu-satunya jalur tulis custody: sisipkan entri baru yang tersambung ke entri terakhir."""
    await _kunci(conn, f"custody:{case_id}")
    akhir = (
        await conn.execute(
            select(custody_event.c.seq, custody_event.c.entry_hash)
            .where(custody_event.c.case_id == case_id)
            .order_by(custody_event.c.seq.desc())
            .limit(1)
        )
    ).first()
    isi = Isi(
        case_id=case_id,
        seq=(akhir.seq if akhir else 0) + 1,
        ts=iso_utc(datetime.now(UTC)) or "",
        actor=actor,
        action=action,
        artifact_id=artifact_id,
        sha256=sha256,
        detail=detail,
    )
    entri = sambung(akhir.entry_hash if akhir else None, isi)
    await conn.execute(custody_event.insert().values(asdict(entri) | {"ts": waktu_utc(entri.ts)}))
    return entri


async def baca_custody(conn: AsyncConnection, case_id: str) -> list[Entri]:
    rows = await conn.execute(
        select(custody_event).where(custody_event.c.case_id == case_id).order_by(custody_event.c.seq)
    )
    return [Entri(**(dict(r._mapping) | {"ts": iso_utc(r.ts)})) for r in rows]


# ---- kasus ----


async def _status(conn: AsyncConnection, case_id: str, lama: str, baru: str, **kolom: Any) -> None:
    if (lama, baru) not in ALUR:
        raise ValueError(f"transisi status tidak sah: {lama} -> {baru}")
    hasil = await conn.execute(
        update(case_file)
        .where(case_file.c.case_id == case_id, case_file.c.status == lama)
        .values(status=baru, updated_at=func.now(), **kolom)
    )
    if hasil.rowcount != 1:
        raise AstriaError("KONFLIK_STATUS_KASUS", f"kasus tidak berstatus {lama}", 409)
    await tambah_custody(conn, case_id, AKTOR, "STATUS", detail=f"{lama}>{baru}")


def _audit(kasus: Mapping[str, Any], action: str, via: str, payload: dict[str, Any]) -> Envelope:
    return audit_event(
        {
            "idempotency_key": f"dfir:{kasus['case_id']}:{action}",
            "occurred_at": datetime.now(UTC).isoformat(),
            "actor": AKTOR,
            "actor_type": "service",
            "action": action,
            "target_type": "case",
            "target_id": kasus["case_id"],
            "via": via,
            "payload": payload,
        },
        correlation={"card_id": kasus["card_id"], "version": kasus["card_version"], "case_id": kasus["case_id"]},
    )


async def kasus(conn: AsyncConnection, case_id: str) -> RowMapping | None:
    row = (await conn.execute(select(case_file).where(case_file.c.case_id == case_id))).first()
    return row._mapping if row else None


async def buat_kasus(conn: AsyncConnection, card_id: str, requested_by: str) -> tuple[RowMapping, bool]:
    """Kasus untuk versi kartu terbaru. Kasus yang sudah ada dan tidak `gagal` dipakai ulang (permintaan ulang aman)."""
    await _kunci(conn, f"kasus:{card_id}")
    k = (
        await conn.execute(
            select(kartu.c.version).where(kartu.c.card_id == card_id).order_by(kartu.c.version.desc()).limit(1)
        )
    ).first()
    if k is None:
        raise AstriaError("KARTU_TIDAK_DITEMUKAN", "kartu belum diterima dfir", 404)
    ada = (
        await conn.execute(
            select(case_file)
            .where(case_file.c.card_id == card_id, case_file.c.card_version == k.version, case_file.c.status != "gagal")
            .order_by(case_file.c.created_at.desc())
            .limit(1)
        )
    ).first()
    if ada is not None:
        return ada._mapping, False

    hari = datetime.now(UTC).strftime("%Y%m%d")
    await _kunci(conn, f"case-id:{hari}")
    n = (await conn.execute(select(func.count()).where(case_file.c.case_id.like(f"CS-{hari}-%")))).scalar_one()
    case_id = f"CS-{hari}-{n + 1:03d}"
    baru = (
        (
            await conn.execute(
                case_file.insert()
                .values(
                    case_id=case_id,
                    card_id=card_id,
                    card_version=k.version,
                    status="diminta",
                    requested_by=requested_by,
                )
                .returning(*case_file.c)
            )
        )
        .one()
        ._mapping
    )
    await tambah_custody(conn, case_id, requested_by, "KASUS_DIBUAT", detail=f"{card_id} v{k.version}")
    await enqueue(conn, _audit(baru, "case.create", "api", {"card_id": card_id, "requested_by": requested_by}))
    log.info("kasus_dibuat", case_id=case_id, card_id=card_id, version=k.version)
    return baru, True


async def bangun_kasus(engine: AsyncEngine, evidence: EvidenceSource, case_id: str) -> None:
    """Dijalankan di latar setelah 202. Galat apa pun (mis. ingest tidak menjawab) membuat kasus `gagal`."""
    try:
        await _bangun(engine, evidence, case_id)
    except Exception as exc:
        log.exception("kasus_gagal_dibangun", case_id=case_id)
        async with engine.begin() as conn:
            row = await kasus(conn, case_id)
            if row is not None and row["status"] == "mengumpulkan":
                await _gagal(conn, row, [f"Kasus dihentikan karena galat: {type(exc).__name__}."])


async def _gagal(conn: AsyncConnection, row: Mapping[str, Any], keterbatasan: list[str]) -> None:
    await _status(conn, row["case_id"], "mengumpulkan", "gagal", keterbatasan=keterbatasan)
    await enqueue(conn, _audit(row, "case.complete", "system", {"status": "gagal"}))
    log.warning("kasus_gagal", case_id=row["case_id"])


def _cek_hash(tercatat: Mapping[str, str], items: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bandingkan `record_sha256` dari ingest dengan hash yang dicatat kartu, per ID bukti."""
    lampiran = []
    for eid, sha_kartu in sorted(tercatat.items()):
        item = items.get(eid)
        sha_ingest = item["record_sha256"] if item else None
        aksi = (
            "BUKTI_TIDAK_DITEMUKAN" if item is None else "HASH_COCOK" if sha_ingest == sha_kartu else "HASH_TIDAK_COCOK"
        )
        lampiran.append({"evidence_id": eid, "sha256_kartu": sha_kartu, "sha256_ingest": sha_ingest, "aksi": aksi})
    return lampiran


async def _bangun(engine: AsyncEngine, evidence: EvidenceSource, case_id: str) -> None:
    async with engine.begin() as conn:
        row = await kasus(conn, case_id)
        if row is None:
            raise ValueError(f"kasus {case_id} tidak ada")
        kode_json = (
            await conn.execute(
                select(kartu.c.kode).where(kartu.c.card_id == row["card_id"], kartu.c.version == row["card_version"])
            )
        ).scalar_one()
        await _status(conn, case_id, "diminta", "mengumpulkan")
    kode = Kode.model_validate(kode_json)
    tercatat = {b.id.root: b.sha256 for b in kode.bukti}
    jawab = await evidence.fetch(list(tercatat))
    items = {i["evidence_id"]: i for i in jawab["items"] if i["evidence_id"] in tercatat}

    async with engine.begin() as conn:
        lampiran = _cek_hash(tercatat, items)
        for b in lampiran:
            detail = f"tercatat di kartu {b['sha256_kartu']}" if b["aksi"] == "HASH_TIDAK_COCOK" else None
            await tambah_custody(
                conn, case_id, AKTOR, b["aksi"], artifact_id=b["evidence_id"], sha256=b["sha256_ingest"], detail=detail
            )
        if salah := [b["evidence_id"] for b in lampiran if b["aksi"] == "HASH_TIDAK_COCOK"]:
            await _gagal(conn, row, [f"Hash bukti tidak cocok dengan kartu: {', '.join(salah)}. Laporan tidak dibuat."])
            return

        linimasa = bangun_linimasa(case_id, items.values())
        if linimasa:
            await conn.execute(timeline_item.insert(), [asdict(i) | {"case_id": case_id} for i in linimasa])
        await tambah_custody(
            conn, case_id, AKTOR, "LINIMASA_DIBUAT", sha256=hash_linimasa(linimasa), detail=f"{len(linimasa)} item"
        )
        keterbatasan = _keterbatasan(lampiran, linimasa)
        await _status(
            conn,
            case_id,
            "mengumpulkan",
            "linimasa_siap",
            window_start=linimasa[0].ts_utc if linimasa else None,
            window_end=linimasa[-1].ts_utc if linimasa else None,
            keterbatasan=keterbatasan,
        )

        html = report.render(
            kasus=row,
            kode=kode,
            items=linimasa,
            custody=await baca_custody(conn, case_id),
            keterbatasan=keterbatasan,
            lampiran=lampiran,
            dibuat=datetime.now(UTC),
        )
        html_sha = sha256_hex(html)
        await conn.execute(
            report_version.insert().values(
                case_id=case_id,
                version=1,
                template_version=report.TEMPLAT,
                html=html,
                html_sha256=html_sha,
                overall_verdict="TEMPLAT",
            )
        )
        await tambah_custody(
            conn, case_id, AKTOR, "TULIS_LAPORAN", artifact_id=f"{case_id}/laporan-v1", sha256=html_sha
        )
        await _status(conn, case_id, "linimasa_siap", "draf_sebagian")
        payload = {"status": "draf_sebagian", "report_version": 1, "html_sha256": html_sha, "items": len(linimasa)}
        await enqueue(conn, _audit(row, "case.complete", "system", payload))
        log.info("kasus_selesai", case_id=case_id, items=len(linimasa))


def _keterbatasan(lampiran: Sequence[Mapping[str, Any]], linimasa: Sequence[Any]) -> list[str]:
    out = list(KETERBATASAN_TETAP)
    if hilang := [b["evidence_id"] for b in lampiran if b["aksi"] == "BUKTI_TIDAK_DITEMUKAN"]:
        out.append(f"Bukti tidak ditemukan di ingest: {', '.join(hilang)}.")
    if any(i.injection_suspect for i in linimasa):
        out.append("Sebagian teks bukti ditandai ingest sebagai mencurigakan dan hanya ditampilkan sebagai data.")
    return out


# ---- baca untuk API ----


async def linimasa_kasus(conn: AsyncConnection, case_id: str, cursor: str | None, limit: int) -> list[RowMapping]:
    q = select(timeline_item).where(timeline_item.c.case_id == case_id)
    if cursor:
        q = q.where(timeline_item.c.item_id > cursor)
    rows = await conn.execute(q.order_by(timeline_item.c.item_id).limit(limit))
    return [r._mapping for r in rows]


async def laporan_terbaru(conn: AsyncConnection, case_id: str, *, html: bool = True) -> RowMapping | None:
    kolom = report_version.c if html else [report_version.c.version, report_version.c.html_sha256]
    row = (
        await conn.execute(
            select(*kolom).where(report_version.c.case_id == case_id).order_by(report_version.c.version.desc()).limit(1)
        )
    ).first()
    return row._mapping if row else None
