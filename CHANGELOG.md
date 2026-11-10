# Changelog

## [Unreleased]

### Added

- dfir#2 versi minimal: konsumen `card.skeleton.created` (durable `dfir-kartu`), `POST /internal/v1/cases` (202, kasus dibangun di latar), `GET` kasus, linimasa berhalaman, custody dengan `chain_ok`, dan laporan HTML. Linimasa deterministik dari bukti kartu (ingest `evidence:batch`), rantai custody berantai hash dengan pemicu append-only di PostgreSQL, hash bukti dicocokkan dengan kartu (tidak cocok berarti `gagal`), laporan Jinja2 autoescape dengan rujukan NIST SP 800-61r3 dan SP 800-86, `audit.record` `case.create`/`case.complete`. Dependensi baru `jinja2`, dev `hypothesis`. Setting `ASTRIA_DFIR_INGEST_URL`. Butuh `astria-contracts` v0.3.0 (`correlation.case_id`) dan ingest dengan `svc:dfir` di `read_callers`.

- Kerangka layanan Tahap 0 dari templat `common` v0.1.0: `/health`, `/ready`, `/metrics`, token layanan, Dockerfile non-root, CI (lint, uji, gitleaks, build image, smoke `/ready`).
- Logo Astria di kepala README (`.github/assets/`, dari kit logo di repo `triase-dfir-uiux`).
