# IRIS-SAT-CLC quality report

## 1. Reproducible reviewer path

The reviewer path uses the task-specific Docker/Compose stack and a deterministic 1,058-feature fixture. It does not require CLMS credentials or the full 1.3 GB official source.

The staging migration creates only the CLC staging table and its indexes/constraints. The reviewer fixture can be loaded twice to demonstrate idempotency, followed by the full test suite and ROI query documented in `README_CLC.md`.

Previously recorded clean-container evidence for the fixture showed:

- first fixture load: `1,058` inserted, `0` rejected/conflicting/duplicate
- identical rerun: `0` inserted, `1,058` unchanged
- destination rows: `1,058`

The current full suite contains **54 passing tests**, covering parser/contract behavior, missing and malformed values, identity, duplicate/conflict handling, geometry/CRS rules, area QA, batch failure semantics, idempotent recovery, staging isolation, CLMS fetch behavior, and ROI behavior.

## 2. Official source provenance

Product: **CLCplus Backbone 2018 Vector**

Verified source metadata:

- package: `CLMS_CLCplus_VECTOR_2018_PU131_CH-IT_03035_V1_1`
- package/file release: `V1_1`
- internal layer: `CLMS_CLCplus_VECTOR_2018_PU131_03035_V1_0`
- reference year: `2018`
- CRS: `EPSG:3035`
- source feature count: `1,413,448`
- MMU: `0.5 ha`
- MMW: `20 m`

The official package was acquired through the authenticated CLMS web workflow on 2026-10-28 (job `3930`).

GeoPackage SHA-256:

`51cf62a809c0421b8c5dc5d25e524bcde5fa83b4934fb5393de7c6ee6b47e204`

Outer archive SHA-256:

`a4ddf8656c61ebea6d4139efe1e2ab5486a83a8a0432b643e7be25e1961a8137`

These hashes are local integrity fingerprints, not publisher-authenticated checksums.

The final-vector `LC_code18` mapping follows the supplied CLC+ Backbone User Manual section 3.3.1 (printed pp. 26–27). All 18 documented vector classes are represented in the contract. Code `254` is kept as NoData and other unmapped codes remain explicit unknowns.

## 3. Full official ingestion

The production loader reads at source in bounded batches of `20,000` features and commits atomically per batch.

### First authoritative full run

| Metric | Result |
|---|---:|
| Features read | 1,413,448 |
| Accepted | 1,413,448 |
| Inserted | 1,313,448 |
| Unchanged | 100,000 |
| Rejected | 0 |
| Conflicting | 0 |
| Duplicate | 0 |
| Geometry/area rejected | 0 |
| Destination rows | 1,413,448 |
| Batches | 71 |
| Elapsed | 5,722.311721 s |
| Peak RSS | 477,245,440 bytes |
| Outcome | success |

The `100,000` unchanged rows were earlier safely committed batches retained during development interruption/restart testing; the authoritative run correctly recognized them by stable identity/fingerprint.

### Full idempotency rerun

| Metric | Result |
|---|---:|
| Features read | 1,413,448 |
| Accepted | 1,413,448 |
| Inserted | 0 |
| Unchanged | 1,413,448 |
| Rejected | 0 |
| Conflicting | 0 |
| Duplicate | 0 |
| Geometry/area rejected | 0 |
| Destination rows | 1,413,448 |
| Batches | 71 |
| Elapsed | 4,127.152891 s |
| Peak RSS | 477,564,928 bytes |
| Outcome | success |

This is full-scale evidence that rerunning the same official release does not create duplicate rows or mutate unchanged records.

Sanitized JSON evidence:

- `docs/evidence/full_ingestion.json`
- `docs/evidence/full_idempotency_rerun.json`

## 4. Database integrity

Verified on the complete official load:

- rows: `1,413,448`
- distinct composite identities: `1,413,448`
- duplicate composite identities: `0`
- invalid stored geometries: `0`
- empty stored geometries: `0`
- stored geometry type: `MULTIPOLYGON`
- stored SRID: `3035`
- `iris_core` schema/object count: `0`

Identity:

`(source_product, package_version, site_id, source_id)`

The fingerprint is separate from identity and is used to distinguish unchanged reruns from conflicting source changes.

The destination has a GiST geometry index.

## 5. Area QA

Implemented tolerances:

- absolute: `0.05 m²`
- relative: `1e-4`

Full-load reconciliation:

- rows outside implemented tolerance: `0`
- maximum absolute difference: approximately `0.000050000001 m²`
- maximum relative difference: approximately `9.95e-09`
- summed source area: `27,710,703,517.476295 m²`
- summed calculated area: `27,710,703,517.5109 m²`

The observed maximum discrepancy is far below the implemented tolerances.

## 6. Full-data ROI result

Fixed ROI:

`POLYGON((4140000 2441000,4142000 2441000,4142000 2443000,4140000 2443000,4140000 2441000))`

CRS: `EPSG:3035`

Measured against the complete official package:

| Metric | Result |
|---|---:|
| Candidate count | 362 |
| Positive-area intersections | 362 |
| ROI area | 4,000,000 m² |
| Covered union area | 3,999,999.9999999995 m² |
| Uncovered area | 4.656612873077393e-10 m² |
| Coverage | effectively 100% |
| Summed intersection area | 4,000,000.0000000005 m² |
| Overlap excess | 9.313225746154785e-10 m² |
| Elapsed | 1.526458 s |

Union coverage and summed intersection area are reported separately so overlap/double-count risk is observable.

The result demonstrates complete coverage for this specific validation ROI within the official package. It does not assert universal geographic completeness or current ground truth.

## 7. Failure and restart semantics

Each ingestion batch is atomic:

- prior committed batches survive a later failure;
- the failing batch rolls back without partial rows;
- a replay from the beginning recognizes prior successful rows as unchanged;
- failed reports preserve `outcome=failed`.

Tests include an injected intermediate-batch failure and subsequent safe recovery.

This is **safe idempotent replay**, not checkpoint continuation.

## 8. Limitations

- Automated CLMS service-key/JWT authentication remains unresolved (`HTTP 400 invalid_grant`), while authenticated web acquisition of the official release succeeded.
- Source Polygon and GeometryCollection normalization are not performed; such records are rejected.
- Source reprojection is not performed; the contract expects EPSG:3035.
- No geometry repair is performed; invalid geometries are rejected.
- Cross-release uniqueness relies on the documented source identity combined with product/package/site identifiers.
- CLCplus Backbone 2018 is a reference-year classification product, not current ground truth.
- Local SHA-256 values prove local integrity only.
- The repository intentionally does not include the 1.3 GB official GeoPackage, raw downloaded metadata renderings, or the full user manual.
- Licensing and attribution should follow the official Copernicus / CLMS product terms for the downloaded release.

## 9. Runtime environment observed during final audit

Application-side:

- Python `3.12.12`
- geopandas `1.1.1`
- pyogrio `0.11.0`
- GDAL `3.10.3`
- shapely `2.1.1`
- GEOS `3.13.1`
- pyproj `3.7.1`
- PROJ `9.5.1`

Database-side:

- PostgreSQL `17.5`
- PostGIS `3.5.2`

The reviewer path uses the pinned task-specific dependency lock and isolated Compose resources.
