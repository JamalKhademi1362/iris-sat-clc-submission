# IRIS-SAT-CLC reviewer guide

This repository contains the task-specific CORINE / CLC+ staging implementation for IRIS-SAT-CLC Task 114. The CLC path is isolated from the legacy IRIS application and writes only to `iris_staging.corine_land_cover_clc`; it does not write to `iris_core`.

## Scope and provenance

The implemented source is **Copernicus Land Monitoring Service (CLMS) — CLCplus Backbone 2018 Vector**.

Verified source metadata:

- package: `CLMS_CLCplus_VECTOR_2018_PU131_CH-IT_03035_V1_1`
- package/file release: `V1_1`
- internal source layer: `CLMS_CLCplus_VECTOR_2018_PU131_03035_V1_0`
- reference year: `2018`
- CRS: `EPSG:3035` (ETRS89-extended / LAEA Europe)
- official feature count: `1,413,448`
- minimum mapping unit (MMU): `0.5 ha`
- minimum mapping width (MMW): `20 m`

The `V1_0` text in the internal layer name is source-layer metadata and is intentionally kept distinct from the package/file release `V1_1`.

The official package was acquired on 2026-10-28 through the authenticated CLMS web workflow (job `3930`). The downloaded GeoPackage SHA-256 was:

`51cf62a809c0421b8c5dc5d25e524bcde5fa83b4934fb5393de7c6ee6b47e204`

The outer archive SHA-256 was:

`a4ddf8656c61ebea6d4139efe1e2ab5486a83a8a0432b643e7be25e1961a8137`

These are **local integrity fingerprints** used to prove byte identity across runs. They are not presented as publisher-authenticated CLMS checksums.

The final vector `LC_code18` mapping follows the supplied CLC+ Backbone User Manual, section 3.3.1 (printed pp. 26–27), including the documented vector classes:

`11, 12, 21, 22, 31, 32, 33, 40, 51, 52, 53, 60, 70, 81, 82, 90, 100, 110`

Code `254` is preserved as NoData. Any other unmapped code remains explicit as `unknown_code`; it is not silently remapped.

A deterministic 1,058-feature fixture is included at `tests/fixtures/clc_pu131_small.gpkg` so a reviewer can reproduce the core workflow without downloading the 1.3 GB official source.

## Quick reviewer run

Requirements:

- Docker Engine
- Docker Compose

No host Python, CLMS credentials, official download, legacy Compose file, or legacy migration is required for the reviewer path.

```powershell
Copy-Item .env.clc.example .env.clc.local
# Replace only the local database password in .env.clc.local.

docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml build clc-app
docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml up -d clc-postgis

docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml run --rm clc-app alembic -c alembic_staging.ini upgrade head

docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml run --rm clc-app python -m app.commands.load_clc --input tests/fixtures/clc_pu131_small.gpkg --layer clc_pu131_small --expected-count 1058 --report-dir reports/clc_runs

docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml run --rm clc-app python -m app.commands.load_clc --input tests/fixtures/clc_pu131_small.gpkg --layer clc_pu131_small --expected-count 1058 --report-dir reports/clc_runs

docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml run --rm clc-app python -m pytest -q

docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml run --rm clc-app python -m app.commands.query_clc_roi --roi-wkt "POLYGON((4140000 2441000,4142000 2441000,4142000 2443000,4140000 2443000,4140000 2441000))" --roi-crs EPSG:3035 --source-product "CLCplus Backbone 2018" --package-version V1_1
```

The second fixture load is the idempotency check.

When finished:

```powershell
docker compose --env-file .env.clc.local -p iris-clc-reviewer -f compose.clc.yml down
```

Do not use `down --volumes`, global prune, or the legacy `docker-compose.yml` for this assignment.

## Schema and ingestion behavior

The standalone staging migration creates `iris_staging.corine_land_cover_clc` with:

- `geometry(MultiPolygon,3035)`
- a GiST geometry index
- composite unique identity:
  `(source_product, package_version, site_id, source_id)`

Normalized records preserve product/release metadata, source layer, metadata edition, reference year, `Site_id`, `ID`, `LC_code18`, class label, quality status, source `Shape_Area`, calculated area, geometry, remaining source attributes, source digest, run ID, and fingerprint.

Identity and fingerprint semantics are deterministic:

- same identity + same fingerprint → `unchanged`
- same identity + different fingerprint → fatal conflict before write
- new identity → insert

The full loader reads the source in bounded batches (default `20,000` features). Each batch is committed atomically. If a batch fails, that batch is rolled back while earlier committed batches remain safe; rerunning from the beginning is an idempotent replay, not checkpoint continuation.

Geometry policy is intentionally strict: null, empty, invalid, Polygon, and GeometryCollection source geometries are rejected; accepted records are MultiPolygon / EPSG:3035. No geometry repair or source reprojection is performed.

Area QA compares source `Shape_Area` with calculated EPSG:3035 geometry area using:

- absolute tolerance: `0.05 m²`
- relative tolerance: `1e-4`

The full official dataset had zero rows outside the implemented tolerance.

## Full official execution evidence

### First authoritative full ingestion

- features read / accepted: `1,413,448 / 1,413,448`
- inserted: `1,313,448`
- unchanged from prior safe committed batches: `100,000`
- rejected / conflicting / duplicate: `0 / 0 / 0`
- destination row count: `1,413,448`
- elapsed: `5,722.311721 s`
- peak RSS: `477,245,440 bytes`
- outcome: `success`

### Full idempotency rerun

- features read / accepted: `1,413,448 / 1,413,448`
- inserted: `0`
- unchanged: `1,413,448`
- rejected / conflicting / duplicate: `0 / 0 / 0`
- destination row count: `1,413,448`
- elapsed: `4,127.152891 s`
- peak RSS: `477,564,928 bytes`
- outcome: `success`

Sanitized evidence is retained in:

- `docs/evidence/full_ingestion.json`
- `docs/evidence/full_idempotency_rerun.json`

The final container test suite reported **54 passed**.

## Full-data ROI validation

The fixed reviewer ROI is an EPSG:3035 2,000 m × 2,000 m rectangle (4,000,000 m²).

Against the complete official load:

- candidate features: `362`
- positive-area intersections: `362`
- covered union area: `3,999,999.9999999995 m²`
- uncovered area: `4.656612873077393e-10 m²`
- coverage: effectively `100%`
- overlap excess: `9.313225746154785e-10 m²`
- elapsed: `1.526458 s`

The query scopes source product and package version, uses indexed `ST_Intersects`, computes only positive-area intersections, and reports union coverage separately from summed intersection area to expose overlap/double-count risk.

This validates the query and shows complete coverage for this specific ROI within the loaded official package. It is not a claim of complete coverage for every possible ROI or of current ground truth.

## Limitations and source notes

- CLCplus Backbone 2018 represents a 2018 reference classification product; it is not a current land-use survey.
- The vector classification should not be interpreted as exact sub-polygon fractional cover.
- Polygon / GeometryCollection source normalization and source reprojection are intentionally unsupported by the strict ingestion contract.
- Cross-release uniqueness relies on the source-system identity combined with product/package/site/source identifiers.
- The local SHA-256 values prove local byte identity, not publisher authenticity.
- Automated CLMS service-key/JWT token exchange returned HTTP 400 `invalid_grant`. The authenticated CLMS web acquisition succeeded, so the official release itself was obtained and validated. No speculative authentication rewrite was introduced.
- Licensing and attribution should follow the official Copernicus / CLMS product terms for the downloaded release; the repository does not redistribute the full official GeoPackage or raw manual.

See:

- `docs/QUALITY_REPORT.md`
- `docs/ACCEPTANCE_MATRIX.md`
- `tests/fixtures/clc_pu131_small.provenance.md`
- `docs/evidence/full_ingestion.json`
- `docs/evidence/full_idempotency_rerun.json`
