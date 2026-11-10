<!-- astria-logo -->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/astria-logo-reverse.svg">
    <img src=".github/assets/astria-logo.svg" alt="Astria" width="320">
  </picture>
</p>

# Astria dfir

Analisis DFIR mendalam: linimasa dan laporan kasus.

> Status: Tahap 1 versi minimal (dfir#2). Kasus dari kartu: linimasa deterministik, rantai penguasaan bukti berantai hash (append-only), dan draf laporan HTML. Belum ada narasi model, PDF, dan bagian IOC/ATT&CK. Repo ini adalah submodule dari workspace `triase-dfir` (folder `dfir/`). Rancangan: `notes/2026-11-06-triase-dfir-app-dfir.md` di repo root. Lajur developer: 2.

## Tech stack

Python 3.14, FastAPI, PostgreSQL 18, NATS JetStream, Jinja2, uv

## Perintah

```text
uv sync                                   # butuh akses ke repo privat contracts dan common (tag git)
uv run ruff format --check . && uv run ruff check .
uv run pytest
sh ../common/template/localtest.sh        # di kontainer py devenv: uji terhadap salinan kerja common dan contracts
```

Menjalankan bersama layanan lain: `pwsh ../devenv/scripts/dev.ps1 up`, lalu `dev.ps1 smoke`.

Uji integrasi (`tests/test_integration.py`, `tests/test_http_db.py`) butuh `ASTRIA_TEST_DATABASE_URL`; di kontainer `py` devenv isi dengan
`postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/postgres`. Tanpa variabel itu uji tersebut dilewati. Uji properti
memakai `hypothesis` (grup dev); `localtest.sh` belum memasangnya, jadi pasang dulu ke venv uji
(`VIRTUAL_ENV=/tmp/astria-localtest-dfir uv pip install hypothesis==6.170.15`).

## API (`/internal/v1`, hanya `svc:api`)

| Endpoint | Hasil |
|---|---|
| `POST /cases` `{"card_id"}` | `202` `{case_id, status, card_id, card_version, links}`; kasus dibangun di latar. Kartu belum diterima: `404 KARTU_TIDAK_DITEMUKAN`. Kasus yang sudah ada dan tidak `gagal` dipakai ulang |
| `GET /cases/{case_id}` | Status, peminta (`act.sub`), jendela waktu, keterbatasan, versi dan hash laporan |
| `GET /cases/{case_id}/timeline?cursor=&limit=` | Item linimasa berhalaman (`next_cursor`), maks 500 |
| `GET /cases/{case_id}/custody` | Semua entri custody dan `chain_ok` (rantai dihitung ulang) |
| `GET /cases/{case_id}/report?format=html` | Draf HTML (CSP tanpa skrip, header `X-Report-SHA256`); belum ada laporan: `409 LAPORAN_BELUM_ADA` |

## Cara kerja

| Bagian | Isi |
|---|---|
| `cases.py` | Konsumen `card.skeleton.created` (durable `dfir-kartu`) menyimpan kode kartu per `(card_id, version)`; kasus memakai versi terbaru. `CS-yyyymmdd-nnn`. Status `diminta` → `mengumpulkan` → `linimasa_siap` → `draf_sebagian` (semua bagian templat tetap), atau `gagal`; tiap transisi jadi entri custody `STATUS`. `audit.record` `case.create` dan `case.complete` lewat outbox |
| `evidence.py` | Bukti kartu dari ingest `POST /svc/v1/evidence:batch` (maks 500 ID per panggilan) dengan token layanan audiens `svc:ingest` |
| `custody.py` | `entry_hash = sha256(prev_hash + orjson(entri, kunci terurut))`, genesis `sha256("dfir-genesis:" + case_id)`. `custody_event` append-only: kode hanya menyisipkan, pemicu PostgreSQL menolak UPDATE, DELETE, dan TRUNCATE |
| Hash bukti | `record_sha256` dari ingest dibandingkan dengan `kode.bukti[].sha256` kartu: `HASH_COCOK`, `HASH_TIDAK_COCOK` (kasus `gagal`, tanpa laporan), atau `BUKTI_TIDAK_DITEMUKAN` (jadi keterbatasan). Ingest tidak menjawab: `gagal` |
| `timeline.py` | Dedup per ID bukti, urut `(occurred_at UTC, evidence_id)`, ID `TL-<yyyymmdd>-<nnn>-<nnnnn>`; jendela kasus = bukti paling awal sampai paling akhir |
| `report.py` | Jinja2 autoescape: Ringkasan (deterministik dari kode kartu, tanpa model), Kronologi (WIB), Rantai penguasaan, Keterbatasan, Lampiran hash, rujukan NIST SP 800-61r3 dan SP 800-86. HTML dan `html_sha256` disimpan di `report_version` |

Konfigurasi: `ASTRIA_DFIR_INGEST_URL` (bawaan `http://ingest:8000`; wajib bersama `ASTRIA_SERVICE_KEY`/`_FILE` bila
`ASTRIA_DATABASE_URL` diisi). ingest harus memasukkan `svc:dfir` di `read_callers`.
Batasan: kasus dibangun sebagai tugas latar di proses yang sama; proses yang mati di tengah meninggalkan kasus `diminta`/`mengumpulkan`.
Belum ada Alembic (tabel dan pemicu dibuat saat start), peran `dfir_app` dengan `REVOKE`, PDF, siklus narasi dan verifier, serta event `case.*`.

## Alur git

Branch `{type}/{yyyyMMdd}-{taskId}-{nama}`; commit `{type}({yyyyMMddHHmm}): [{taskId}] {pesan}`, satu baris, tanpa trailer. `main` hanya berubah lewat pull request (merge rebase); jangan push langsung ke `main` dan jangan `--no-verify`.
