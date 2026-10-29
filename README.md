# IRIS-SAT-CLC — CLC+ Vector Ingestion

Submission for **IRIS-SAT-CLC / Task 114 — CORINE Land Cover (CLC+) Vector Ingestion**.

The implementation adds an isolated, reproducible CLC+ staging pipeline for the official **Copernicus Land Monitoring Service CLCplus Backbone 2018 Vector** product.

## Start here

➡️ **[Reviewer guide and run instructions](README_CLC.md)**

Supporting evidence:

- [Quality report](docs/QUALITY_REPORT.md)
- [Acceptance matrix](docs/ACCEPTANCE_MATRIX.md)
- [Full ingestion evidence](docs/evidence/full_ingestion.json)
- [Full idempotency rerun evidence](docs/evidence/full_idempotency_rerun.json)
- [Full-data ROI evidence](docs/evidence/roi_full.json)

## Verified final results

- Official source features: **1,413,448**
- Full ingestion: **success**
- Full rerun: **0 inserted / 1,413,448 unchanged**
- Rejected / conflicting / duplicate: **0 / 0 / 0**
- Final tests: **54 passed**
- Full-data ROI coverage for the validation ROI: **~100%**
- Peak RSS during full ingestion/rerun: **~455 MiB**

The reviewer path uses the included 1,058-feature fixture and Docker Compose; downloading the full official dataset is not required.
