from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from collections import Counter
from collections.abc import Iterable
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text

from app.clc_contract import CanonicalRecord, classify_identities
from app.database.repositories.clc_repository import ClcRepository
from app.parsers.clc_parser import ParseResult


class IdentityConflictError(RuntimeError):
    pass


class InjectedLoadFailure(RuntimeError):
    """Test-only failure hook used to verify loader transaction rollback."""


def load_parse_result(
    parse_result: ParseResult,
    database_url: str,
    report_dir: str | Path,
    failure_after_inserts: int | None = None,
) -> dict[str, Any]:
    run_id = str(uuid4())
    started = time.perf_counter()
    classification = classify_identities(list(parse_result.records))
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "input_file": parse_result.input_path,
        "input_sha256": parse_result.input_sha256,
        "source_layer": parse_result.source_layer,
        "counts": {
            "input": parse_result.source_feature_count,
            "valid": len(parse_result.records),
            "rejected": len(parse_result.rejected),
            "nodata": sum(record.quality_status == "nodata" for record in parse_result.records),
            "unknown_code": sum(record.quality_status == "unknown_code" for record in parse_result.records),
            "duplicate": classification.duplicate_count,
            "conflicting": len(classification.conflicts),
            "inserted": 0,
            "unchanged": 0,
            "written": 0,
            "committed": 0,
            "rolled_back": 0,
        },
        "transaction_committed": False,
        "transaction_rolled_back": False,
        "outcome": "failed",
        "rejections": [item.as_dict() for item in parse_result.rejected],
        "errors": [],
    }
    transaction_started = False
    engine = None

    try:
        if classification.conflicts:
            first, second = classification.conflicts[0]
            raise IdentityConflictError(
                "input identity conflict for "
                f"{first.identity}: rows {first.row_number} and {second.row_number}"
            )

        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.begin() as connection:
            transaction_started = True
            repository = ClcRepository(connection)
            identities = [record.identity for record in classification.unique_records]
            existing = repository.existing_fingerprints(identities)
            for record in classification.unique_records:
                previous_fingerprint = existing.get(record.identity)
                if previous_fingerprint is not None and previous_fingerprint != record.record_fingerprint:
                    raise IdentityConflictError(
                        "database identity conflict for "
                        f"{record.identity} at input row {record.row_number}"
                    )

            unchanged = sum(
                record.identity in existing for record in classification.unique_records
            )
            inserted = 0
            for record in classification.unique_records:
                if record.identity not in existing:
                    if not repository.insert(record, run_id, parse_result.input_sha256):
                        raise IdentityConflictError(
                            f"concurrent identity conflict for {record.identity}"
                        )
                    inserted += 1
                    report["counts"]["inserted"] = inserted
                    if failure_after_inserts is not None and inserted >= failure_after_inserts:
                        raise InjectedLoadFailure(
                            f"injected failure after {inserted} insert(s)"
                        )

            report["counts"].update(
                {
                    "inserted": inserted,
                    "unchanged": unchanged,
                    "written": inserted,
                    "committed": inserted,
                }
            )
            report["transaction_committed"] = True
        report["outcome"] = "success"
    except Exception as exc:
        if transaction_started:
            report["counts"]["rolled_back"] = report["counts"].get("inserted", 0)
            report["counts"]["written"] = 0
            report["counts"]["inserted"] = 0
            report["counts"]["committed"] = 0
            report["transaction_rolled_back"] = True
        report["errors"].append(_safe_error(exc))
    finally:
        if engine is not None:
            try:
                with engine.connect() as connection:
                    report["destination_row_count"] = connection.execute(
                        text("SELECT count(*) FROM iris_staging.corine_land_cover_clc")
                    ).scalar_one()
            except Exception as exc:
                report["destination_row_count"] = None
                report["errors"].append(_safe_error(exc))
            engine.dispose()

        report["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_report(report, report_dir)

    return report


def load_parse_batches(
    parse_batches: Iterable[ParseResult],
    *,
    source_feature_count: int,
    input_file: str,
    input_sha256: str,
    source_layer: str,
    database_url: str,
    report_dir: str | Path,
    batch_size: int,
    failure_batch_number: int | None = None,
) -> dict[str, Any]:
    """Load bounded read batches with one atomic transaction per batch.

    Earlier committed batches remain after a later batch failure. Stable
    identities and fingerprints make a complete rerun safe: prior batches are
    counted as unchanged and processing resumes through the remaining input.
    """
    if batch_size <= 0:
        raise ValueError("batch size must be a positive integer")
    if failure_batch_number is not None and failure_batch_number <= 0:
        raise ValueError("failure batch number must be positive")

    run_id = str(uuid4())
    started = time.perf_counter()
    report_directory = Path(report_dir)
    report_directory.mkdir(parents=True, exist_ok=True)
    rejection_path = report_directory / f"{run_id}.rejections.jsonl"
    reason_counts: Counter[str] = Counter()
    rss_before = _current_rss_bytes()
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "input_file": input_file,
        "input_sha256": input_sha256,
        "source_layer": source_layer,
        "source_feature_count_expected": source_feature_count,
        "batch_size": batch_size,
        "batches_read": 0,
        "batches_committed": 0,
        "batch_transaction_model": "atomic_per_batch_idempotent_rerun",
        "counts": {
            "input": 0,
            "features_read": 0,
            "valid": 0,
            "accepted": 0,
            "rejected": 0,
            "nodata": 0,
            "unknown_code": 0,
            "duplicate": 0,
            "conflicting": 0,
            "inserted": 0,
            "unchanged": 0,
            "written": 0,
            "committed": 0,
            "rolled_back": 0,
            "geometry_area_rejected": 0,
        },
        "rejection_reason_counts": {},
        "rejection_report_path": str(rejection_path),
        "transaction_committed": False,
        "transaction_rolled_back": False,
        "partial_commits": False,
        "outcome": "failed",
        "errors": [],
        "memory": {
            "rss_before_bytes": rss_before,
            "rss_peak_bytes": _peak_rss_bytes(),
            "rss_after_bytes": None,
        },
    }
    engine = None
    inserted_in_current_batch = 0

    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    CREATE TEMP TABLE clc_ingest_seen (
                        source_product text NOT NULL,
                        package_version text NOT NULL,
                        site_id integer NOT NULL,
                        source_id bigint NOT NULL,
                        record_fingerprint char(64) NOT NULL,
                        PRIMARY KEY (
                            source_product, package_version, site_id, source_id
                        )
                    ) ON COMMIT PRESERVE ROWS
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TEMP TABLE clc_ingest_batch (
                        source_product text NOT NULL,
                        package_version text NOT NULL,
                        site_id integer NOT NULL,
                        source_id bigint NOT NULL,
                        record_fingerprint char(64) NOT NULL,
                        PRIMARY KEY (
                            source_product, package_version, site_id, source_id
                        )
                    ) ON COMMIT PRESERVE ROWS
                    """
                )
            )
            connection.commit()

            for batch_number, parse_result in enumerate(parse_batches, start=1):
                inserted_in_current_batch = 0
                report["batches_read"] = batch_number
                counts = report["counts"]
                counts["input"] += parse_result.source_feature_count
                counts["features_read"] += parse_result.source_feature_count
                counts["valid"] += len(parse_result.records)
                counts["accepted"] += len(parse_result.records)
                counts["rejected"] += len(parse_result.rejected)
                counts["nodata"] += sum(
                    record.quality_status == "nodata"
                    for record in parse_result.records
                )
                counts["unknown_code"] += sum(
                    record.quality_status == "unknown_code"
                    for record in parse_result.records
                )
                for rejected in parse_result.rejected:
                    reason_counts[rejected.reason] += 1
                    if _is_geometry_or_area_reason(rejected.reason):
                        counts["geometry_area_rejected"] += 1
                _append_rejections(rejection_path, parse_result.rejected)

                classification = classify_identities(list(parse_result.records))
                counts["duplicate"] += classification.duplicate_count
                if classification.conflicts:
                    counts["conflicting"] += len(classification.conflicts)
                    first, second = classification.conflicts[0]
                    raise IdentityConflictError(
                        "input identity conflict for "
                        f"{first.identity}: rows {first.row_number} and "
                        f"{second.row_number}"
                    )

                with connection.begin():
                    repository = ClcRepository(connection)
                    unique_records = list(classification.unique_records)
                    repository.stage_batch_records(unique_records)
                    run_existing = repository.staged_run_fingerprints()
                    new_run_records: list[CanonicalRecord] = []
                    for record in unique_records:
                        prior_run_fingerprint = run_existing.get(record.identity)
                        if prior_run_fingerprint is None:
                            new_run_records.append(record)
                        elif prior_run_fingerprint == record.record_fingerprint:
                            counts["duplicate"] += 1
                        else:
                            counts["conflicting"] += 1
                            raise IdentityConflictError(
                                "cross-batch input identity conflict for "
                                f"{record.identity} at input row {record.row_number}"
                            )

                    repository.remember_run_records(new_run_records)
                    existing = repository.staged_destination_fingerprints()
                    for record in new_run_records:
                        previous_fingerprint = existing.get(record.identity)
                        if (
                            previous_fingerprint is not None
                            and previous_fingerprint != record.record_fingerprint
                        ):
                            counts["conflicting"] += 1
                            raise IdentityConflictError(
                                "database identity conflict for "
                                f"{record.identity} at input row {record.row_number}"
                            )

                    unchanged_in_batch = sum(
                        record.identity in existing for record in new_run_records
                    )
                    records_to_insert = [
                        record
                        for record in new_run_records
                        if record.identity not in existing
                    ]
                    if failure_batch_number == batch_number:
                        for record in records_to_insert:
                            if not repository.insert(
                                record, run_id, parse_result.input_sha256
                            ):
                                counts["conflicting"] += 1
                                raise IdentityConflictError(
                                    f"concurrent identity conflict for {record.identity}"
                                )
                            inserted_in_current_batch += 1
                            if inserted_in_current_batch >= 1:
                                raise InjectedLoadFailure(
                                    "injected failure in batch "
                                    f"{batch_number} after one insert"
                                )
                    else:
                        inserted_in_current_batch = repository.insert_many(
                            records_to_insert,
                            run_id,
                            parse_result.input_sha256,
                        )
                        if inserted_in_current_batch != len(records_to_insert):
                            counts["conflicting"] += (
                                len(records_to_insert) - inserted_in_current_batch
                            )
                            raise IdentityConflictError(
                                "concurrent identity conflict during batch insert"
                            )

                counts["inserted"] += inserted_in_current_batch
                counts["unchanged"] += unchanged_in_batch
                counts["written"] += inserted_in_current_batch
                counts["committed"] += inserted_in_current_batch
                report["batches_committed"] = batch_number
                report["memory"]["rss_peak_bytes"] = _max_rss(
                    report["memory"]["rss_peak_bytes"],
                    _peak_rss_bytes(),
                    _current_rss_bytes(),
                )
                inserted_in_current_batch = 0

            if report["counts"]["features_read"] != source_feature_count:
                raise RuntimeError(
                    "source feature reconciliation failed: expected "
                    f"{source_feature_count}, read "
                    f"{report['counts']['features_read']}"
                )
            report["transaction_committed"] = True
            report["outcome"] = "success"
    except Exception as exc:
        if inserted_in_current_batch:
            report["counts"]["rolled_back"] += inserted_in_current_batch
            report["transaction_rolled_back"] = True
        report["partial_commits"] = report["batches_committed"] > 0
        report["errors"].append(_safe_error(exc))
    finally:
        report["rejection_reason_counts"] = dict(sorted(reason_counts.items()))
        if engine is not None:
            try:
                with engine.connect() as connection:
                    report["destination_row_count"] = connection.execute(
                        text("SELECT count(*) FROM iris_staging.corine_land_cover_clc")
                    ).scalar_one()
            except Exception as exc:
                report["destination_row_count"] = None
                report["errors"].append(_safe_error(exc))
            engine.dispose()

        report["memory"]["rss_after_bytes"] = _current_rss_bytes()
        peak_values = [
            value
            for value in (
                report["memory"]["rss_peak_bytes"],
                _peak_rss_bytes(),
                report["memory"]["rss_after_bytes"],
            )
            if value is not None
        ]
        report["memory"]["rss_peak_bytes"] = max(peak_values) if peak_values else None
        report["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_report(report, report_dir)

    return report


def _append_rejections(path: Path, rejected_records: Iterable[Any]) -> None:
    records = list(rejected_records)
    if not records:
        return
    with path.open("a", encoding="utf-8") as handle:
        for rejected in records:
            handle.write(
                json.dumps(rejected.as_dict(), sort_keys=True, ensure_ascii=False)
                + "\n"
            )


def _is_geometry_or_area_reason(reason: str) -> bool:
    return reason.startswith("geometry") or "area" in reason


def _current_rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _peak_rss_bytes() -> int | None:
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except (ImportError, AttributeError, ValueError):
        return _current_rss_bytes()


def _max_rss(*values: int | None) -> int | None:
    measured = [value for value in values if value is not None]
    return max(measured) if measured else None


def _write_report(report: dict[str, Any], report_dir: str | Path) -> Path:
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / f"{report['run_id']}.json"
    report["report_path"] = str(report_path)
    temporary_path = report_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(report_path)
    return report_path


def _safe_error(error: Exception) -> str:
    message = str(error)
    return re.sub(r"(://[^:/]+:)[^@]+(@)", r"\1***\2", message)
