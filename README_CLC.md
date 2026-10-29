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
- dataset DOI: `https://doi.org/10.2909/d45d5114-fb86-4265-9c5a-a7225a511f7c`

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

### PU131 scope

This submission is configured by default for the verified PU131 official release used for the assignment. The source layer can be overridden explicitly with `--layer`. `Site_id` is read from the source dataset rather than hard-coded in parser, loader, or repository logic.

### Source attribution and thematic uncertainty

The official CLMS dataset page lists **95% overall thematic accuracy** for the CLCplus Backbone 2018 vector product. The Product User Manual provides more nuance: for the full EEA38+UK product, the blind-analysis validation stage reports approximately `63.2% ± 0.6%`, while the subsequent plausibility-analysis validation stage reports approximately `91.1% ± 0.4%`, against a 95% target. These figures are not specific to PU131 (CH-IT). For downstream GIS/EO use, the product should therefore be treated as a classified 2018 land-cover reference product with documented uncertainty rather than as a precise measurement of current ground cover.

For derived products or services, CLMS data policy recommends the attribution:

> Generated using European Union's Copernicus Land Monitoring Service information; https://doi.org/10.2909/d45d5114-fb86-4265-9c5a-a7225a511f7c

## Quick reviewer run

Requirements:

- Docker Engine
- Docker Compose

No host Python, CLMS credentials, official download, legacy Compose file, or legacy migration is required for the reviewer path.

### PowerShell

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

### Bash / sh

Use the same commands; only the initial copy command differs:

```bash
cp .env.clc.example .env.clc.local
```

The second fixture load is the idempotency check.

> **Fixture ROI note:** the included 1,058-feature fixture is intentionally a small, incomplete subset of the official package. On a fixture-only database, the fixed ROI returns `164` positive-area intersections and about `56.683646%` coverage. The missing coverage is explained by the fixture export containing no polygons from classes `11`, `12`, and `82`; in the full official load, those classes account for the missing `1,732,654 m²`. Against the complete official ingestion, the same ROI returns `362` intersections and effectively `100%` coverage. The full-data evidence is documented below and in `docs/evidence/roi_full.json`.

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

## Full official dataset reproduction

The reviewer path above intentionally uses the included fixture. The complete official dataset was also ingested and validated separately.

Official package:

- product: `Copernicus Land Monitoring Service CLCplus Backbone 2018 Vector`
- package: `CLMS_CLCplus_VECTOR_2018_PU131_CH-IT_03035_V1_1`
- source layer: `CLMS_CLCplus_VECTOR_2018_PU131_03035_V1_0`
- CRS: `EPSG:3035`
- expected feature count: `1,413,448`
- GeoPackage SHA-256: `51cf62a809c0421b8c5dc5d25e524bcde5fa83b4934fb5393de7c6ee6b47e204`

Example full-load command, with the directory containing the official GeoPackage mounted read-only into the container:

```powershell
docker compose --env-file .env.clc.local -p iris-clc-full -f compose.clc.yml run --rm `
  -v "C:\path\to\clc-data:/data:ro" `
  clc-app python -m app.commands.load_clc `
  --input /data/CLMS_CLCplus_VECTOR_2018_PU131_CH-IT_03035_V1_1.gpkg `
  --layer CLMS_CLCplus_VECTOR_2018_PU131_03035_V1_0 `
  --expected-sha256 51cf62a809c0421b8c5dc5d25e524bcde5fa83b4934fb5393de7c6ee6b47e204 `
  --expected-count 1413448 `
  --batch-size 20000 `
  --report-dir reports/clc_runs
```

The exact extracted GeoPackage filename may differ from the outer package name; use the verified local `.gpkg` basename while keeping the layer name, checksum, and expected count shown above.

The first authoritative full ingestion took about **95 minutes** in the validation environment and remained bounded at approximately **455 MiB peak RSS**. Full-scale execution is retained as evidence of scale behavior; it is not required for the reviewer quick-start.

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

The `100,000` unchanged rows were not missing work: an earlier controlled validation run had already committed the first five 20,000-feature batches. The authoritative full run safely reconciled those committed rows rather than duplicating them, then inserted the remaining `1,313,448` rows.

### Full idempotency rerun

- features read / accepted: `1,413,448 / 1,413,448`
- inserted: `0`
- unchanged: `1,413,448`
- rejected / conflicting / duplicate: `0 / 0 / 0`
- destination row count: `1,413,448`
- elapsed: `4,127.152891 s`
- peak RSS: `477,564,928 bytes`
- outcome: `success`

The complete rerun demonstrates safe idempotent replay of the official dataset.

Sanitized evidence is retained in:

- `docs/evidence/full_ingestion.json`
- `docs/evidence/full_idempotency_rerun.json`

The final clean reviewer container test suite reported **54 passed, 2 warnings**.

## Full-data ROI validation

The fixed reviewer ROI is an EPSG:3035 2,000 m × 2,000 m rectangle (`4,000,000 m²`).

Against the complete official load:

- candidate features: `362`
- positive-area intersections: `362`
- ROI area: `4,000,000 m²`
- covered union area: `3,999,999.9999999995 m²`
- uncovered area: `4.656612873077393e-10 m²`
- coverage: `99.99999999999999%`
- overlap excess: approximately `0 m²` (`-1.3969838619232178e-09 m²`, floating-point noise)
- elapsed: `2.085635 s`

Evidence: `docs/evidence/roi_full.json`.

The query scopes source product and package version, uses indexed `ST_Intersects`, computes only positive-area intersections, and reports union coverage separately from summed intersection area to expose overlap/double-count risk.

This validates the query and shows complete coverage for this specific ROI within the loaded official package. It is not a claim of complete coverage for every possible ROI or of current ground truth.

## Acquisition limitation

The automated CLMS service-key JWT token exchange was implemented against the published authentication flow, but the live token endpoint returned HTTP 400 `invalid_grant`. No client-request conformance defect was identified during the assignment. The official package used for validation was therefore acquired through the authenticated CLMS web workflow.

This limitation affects **automated acquisition only**. Parsing, validation, bounded ingestion, idempotency, database QA, spatial querying, and evidence generation were completed successfully.

## Limitations and source notes

- CLCplus Backbone 2018 represents a 2018 reference classification product; it is not a current land-use survey.
- The vector classification should not be interpreted as exact sub-polygon fractional cover.
- The official product page lists 95% overall thematic accuracy. The Product User Manual reports two validation stages for the full EEA38+UK product: approximately `63.2% ± 0.6%` for blind analysis and `91.1% ± 0.4%` for plausibility analysis, against the 95% target; these figures are not PU131-specific.
- Polygon / GeometryCollection source normalization and source reprojection are intentionally unsupported by the strict ingestion contract.
- Cross-release uniqueness relies on the source-system identity combined with product/package/site/source identifiers.
- The local SHA-256 values prove local byte identity, not publisher authenticity.
- The submission defaults to the verified PU131 layer; alternative source layers must be supplied explicitly with `--layer`.
- The repository does not redistribute the complete official GeoPackage or the raw product manual.
- Attribution and downstream reuse should follow the official CLMS data policy.

Official source references:

- Dataset: `https://land.copernicus.eu/en/products/clc-backbone/clc-backbone-vector-2018`
- Dataset DOI: `https://doi.org/10.2909/d45d5114-fb86-4265-9c5a-a7225a511f7c`
- CLMS data policy: `https://land.copernicus.eu/en/data-policy`
- CLCplus Backbone 2018 Product User Manual: `https://land.copernicus.eu/en/technical-library/clc-backbone-product-user-manual`

Repository evidence:

- `docs/QUALITY_REPORT.md`
- `docs/ACCEPTANCE_MATRIX.md`
- `tests/fixtures/clc_pu131_small.provenance.md`
- `docs/evidence/full_ingestion.json`
- `docs/evidence/full_idempotency_rerun.json`
- `docs/evidence/roi_full.json`
