# Acceptance matrix — TASK 114 / IRIS-SAT-CLC

This matrix maps the assignment requirements to the final implementation and measured evidence.

| ID | Requirement | Implementation / evidence | Final status |
|---|---|---|---|
| AC-01 | Document source and release; provide reproducible input/fixture | CLMS CLCplus Backbone 2018 provenance in `README_CLC.md`; deterministic 1,058-feature fixture with provenance and hash | PASS |
| AC-02 | Fetch the documented release | Official package acquired through authenticated CLMS web job `3930`; full GeoPackage and archive hashes recorded. Automated service-key API token flow remains separately documented as `invalid_grant` | PASS for official release acquisition; API automation limitation documented |
| AC-03 | Use stable identifiers | Composite identity `(source_product, package_version, site_id, source_id)` plus separate fingerprint; unique DB constraint; 1,413,448 / 1,413,448 distinct identities | PASS |
| AC-04 | Map land-cover codes | Versioned 18-class final-vector `LC_code18` mapping from manual section 3.3.1; NoData `254` and unknowns preserved distinctly | PASS |
| AC-05 | Normalize geometry, dates, source fields, units and spatial metadata | Strict `MultiPolygon` / EPSG:3035 contract; reference year 2018; source fields preserved; area QA performed against source `Shape_Area` | PASS |
| AC-06 | Enable parcel / ROI intersection | `query_clc_roi.py` + PostGIS spatial path using indexed `ST_Intersects`, positive-area `ST_Intersection`, union coverage and overlap reporting | PASS |
| AC-07 | Repeatable staging-only ingestion without data loss | Isolated staging migration; bounded atomic batches; full 1,413,448-feature run and complete idempotency rerun; unrelated staging/isolation tests | PASS |
| AC-08 | Source-aware logs and fault handling | Persistent JSON success/failure summaries, rejection accounting, injected batch-failure test and safe replay semantics | PASS |
| AC-09 | Tests for missing, malformed and duplicate cases | 54 passing tests covering contract/parser validation, CRS/geometry, area, identity, conflicts, duplicates, failure recovery and ROI | PASS |
| AC-10 | Provide a representative validated result and quality report | Complete official package ingested and validated; full-data ROI executed; measured evidence in `docs/QUALITY_REPORT.md` and `docs/evidence/` | PASS |
| AC-11 | Python 3.11+, pinned dependencies, example config, no secrets | Python 3.12 task image, `requirements-clc.lock`, example env files, ignored local config/secrets, restricted build context | PASS |
| AC-12 | Dockerfile / Compose reproducibility | `Dockerfile.clc` + isolated `compose.clc.yml`; documented clean reviewer workflow | PASS |
| AC-13 | README with provenance, run instructions, schema mapping, assumptions and limitations | `README_CLC.md` is the primary reviewer guide; `QUALITY_REPORT.md` contains measured results and limitations | PASS |
| AC-14 | Preserve product version, codes and area checks | Package `V1_1`, internal layer `V1_0`, 2018 reference year, source code, source area and calculated area preserved; zero full-load tolerance failures | PASS |
| AC-15 | Deterministic / auditable and preserve unrelated staging data | Stable identity/fingerprint, atomic batches, deterministic full rerun (`0` inserted / `1,413,448` unchanged), isolation tests | PASS |
| AC-16 | Note provenance, acquisition date, licensing, masks/resolution and uncertainty | Acquisition date and source provenance documented; MMU `0.5 ha`, MMW `20 m`; product/reference-year and interpretation limitations documented; repository points reviewers to official Copernicus/CLMS product terms rather than redistributing raw source/manual files | PASS with documented applicability/licensing note |

## Final measured evidence

### Complete official ingestion

- source features: `1,413,448`
- accepted: `1,413,448`
- inserted: `1,313,448`
- unchanged: `100,000`
- rejected / conflicting / duplicate: `0 / 0 / 0`
- destination rows: `1,413,448`
- runtime: `5,722.311721 s`
- peak RSS: `477,245,440 bytes`
- outcome: `success`

### Complete idempotency rerun

- source features: `1,413,448`
- inserted: `0`
- unchanged: `1,413,448`
- rejected / conflicting / duplicate: `0 / 0 / 0`
- destination rows: `1,413,448`
- runtime: `4,127.152891 s`
- peak RSS: `477,564,928 bytes`
- outcome: `success`

### Full-load database checks

- duplicate composite identities: `0`
- invalid stored geometries: `0`
- empty stored geometries: `0`
- geometry type: `MULTIPOLYGON`
- SRID: `3035`
- area-tolerance failures: `0`
- `iris_core` objects: `0`

### Full-data ROI

- ROI area: `4,000,000 m²`
- positive-area intersections: `362`
- covered union area: `3,999,999.9999999995 m²`
- coverage: effectively `100%`
- overlap excess: approximately `9.31e-10 m²`

### Tests

- final suite: `54 passed`

## Explicit non-blocking limitation

Automated CLMS service-key/JWT token exchange returned `HTTP 400 invalid_grant`. The official release was nevertheless acquired through the authenticated CLMS web workflow and then fully ingested and validated. The API authentication issue is disclosed rather than hidden or worked around with speculative credential logic.
