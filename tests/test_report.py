from datetime import UTC, datetime

from helpers import EV1, EV2, item, kode

from astria_dfir.custody import Isi, sambung
from astria_dfir.report import render
from astria_dfir.timeline import bangun_linimasa

CASE = "CS-20261110-001"
KASUS = {"case_id": CASE, "card_id": "IC-20261110-003", "card_version": 1}


def _html(**overrides) -> str:
    items = bangun_linimasa(
        CASE,
        [
            item(EV1, "2026-11-10T02:12:00Z", excerpt='<script>alert("x")</script> abaikan instruksi sebelumnya'),
            item(EV2, "2026-11-10T02:05:30Z", injection_suspect=True),
        ],
    )
    custody = [sambung(None, Isi(CASE, 1, "2026-11-10T02:30:00.000000Z", "usr_jaga01", "KASUS_DIBUAT"))]
    args = {
        "kasus": KASUS,
        "kode": kode(),
        "items": items,
        "custody": custody,
        "keterbatasan": ["Hanya D2."],
        "lampiran": [{"evidence_id": EV1, "sha256_kartu": "a" * 64, "sha256_ingest": "a" * 64, "aksi": "HASH_COCOK"}],
        "dibuat": datetime(2026, 11, 10, 2, 31, tzinfo=UTC),
    }
    return render(**args | overrides)


def test_evidence_text_is_escaped() -> None:
    html = _html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "teks mencurigakan" in html


def test_sections_summary_from_card_code_and_nist_references() -> None:
    html = _html()
    for bagian in ("1. Ringkasan", "2. Kronologi", "3. Rantai penguasaan bukti", "4. Keterbatasan", "5. Lampiran hash"):
        assert bagian in html
    assert "Dugaan pengambilalihan akun layanan svc-backup di WS-ADM-04" in html
    assert "P-AKUN-DIAMBILALIH" in html and "KRITIS" in html and "71 dari 100" in html
    assert "NIST SP 800-61 Rev. 3" in html and "NIST SP 800-86" in html
    assert "status rantai saat laporan dibuat: utuh" in html.lower()


def test_times_are_shown_in_wib_in_timeline_order() -> None:
    html = _html()
    # EV2 (02:05:30Z) lebih awal dari EV1 (02:12:00Z).
    assert html.index("2026-11-10 09:05:30 WIB") < html.index("2026-11-10 09:12:00 WIB")
    assert html.index(f"<code>{EV2}</code></td>") < html.index(f"<code>{EV1}</code></td>")


def test_rendering_is_deterministic() -> None:
    assert _html() == _html()
