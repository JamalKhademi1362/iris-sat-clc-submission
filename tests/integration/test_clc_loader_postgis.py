from __future__ import annotations

import json
import os
import re
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from shapely.geometry import MultiPolygon, box
from sqlalchemy import create_engine, text

from app.clc_contract import build_record
from app.loaders.clc_loader import load_parse_batches, load_parse_result
from app.parsers.clc_parser import ParseResult


load_dotenv(".env.clc.local")


@pytest.fixture
def database_url():
    value = os.getenv("CLC_DATABASE_URL")
    if not value:
        pytest.skip("CLC_DATABASE_URL is not configured")
    return value


def _parse_result(records):
    return ParseResult(
        input_path="ph03-synthetic.gpkg",
        input_sha256="a" * 64,
        source_layer="synthetic",
        source_feature_count=len(records),
        records=tuple(records),
        rejected=(),
    )


def _record(source_id: int, site_id: int, extra: str = "stable"):
    geometry = MultiPolygon([box(4140000 + source_id % 10, 2440000, 4140010 + source_id % 10, 2440010)])
    return build_record(
        source_id,
        {
            "ID": source_id,
            "Site_id": site_id,
            "LC_code18": 21,
            "Ref_year": 2018,
            "Shape_Area": geometry.area,
            "extra": extra,
        },
        geometry,
    )


def _rows_for_identity(connection, site_id: int):
    return connection.execute(
        text(
            "SELECT source_product, package_version, site_id, source_id, "
            "record_fingerprint, source_attributes::text "
            "FROM iris_staging.corine_land_cover_clc WHERE site_id = :site"
        ),
        {"site": site_id},
    ).mappings().all()


def test_failed_load_rolls_back_and_preserves_isolation(database_url, tmp_path):
    engine = create_engine(database_url)
    records = [_record(970000001, 970001), _record(970000002, 970002)]
    unrelated_name = "ph03_unrelated_" + records[0].record_fingerprint[:12]
    assert re.fullmatch(r"ph03_unrelated_[0-9a-f]+", unrelated_name)
    try:
        with engine.begin() as connection:
            baseline_total = connection.execute(
                text("SELECT count(*) FROM iris_staging.corine_land_cover_clc")
            ).scalar_one()
            connection.execute(
                text(
                    f"CREATE TABLE iris_staging.{unrelated_name} "
                    "(marker integer NOT NULL, note text NOT NULL)"
                )
            )
            connection.execute(
                text(f"INSERT INTO iris_staging.{unrelated_name} VALUES (7, 'untouched')")
            )
            baseline_columns = connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'iris_staging' AND table_name = :name "
                    "ORDER BY ordinal_position"
                ),
                {"name": unrelated_name},
            ).all()

        report = load_parse_result(
            _parse_result(records),
            database_url,
            tmp_path,
            failure_after_inserts=1,
        )
        assert report["outcome"] == "failed"
        assert report["transaction_rolled_back"] is True
        assert report["counts"]["rolled_back"] == 1
        assert report["counts"]["inserted"] == 0
        assert "injected failure" in report["errors"][0]
        report_path = tmp_path / f"{report['run_id']}.json"
        assert json.loads(report_path.read_text(encoding="utf-8"))["outcome"] == "failed"

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM iris_staging.corine_land_cover_clc")
            ).scalar_one() == baseline_total
            assert _rows_for_identity(connection, 970001) == []
            assert connection.execute(
                text(f"SELECT marker, note FROM iris_staging.{unrelated_name}")
            ).all() == [(7, "untouched")]
            assert connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'iris_staging' AND table_name = :name "
                    "ORDER BY ordinal_position"
                ),
                {"name": unrelated_name},
            ).all() == baseline_columns
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS iris_staging.{unrelated_name}"))
            connection.execute(
                text(
                    "DELETE FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id IN (970001, 970002)"
                )
            )
        engine.dispose()


def test_unrelated_product_version_is_unchanged(database_url, tmp_path):
    engine = create_engine(database_url)
    sentinel_site = 970003
    sentinel_source = 970000003
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO iris_staging.corine_land_cover_clc "
                    "(source_product, package_version, source_layer_name, metadata_edition, "
                    "reference_year, site_id, source_id, lc_code18, class_label, quality_status, "
                    "source_shape_area, calculated_area_m2, geometry, source_attributes, "
                    "source_file_sha256, run_id, record_fingerprint) "
                    "VALUES ('PH03_SENTINEL_PRODUCT', 'PH03_OTHER_VERSION', "
                    "'synthetic', 'test', 2018, :site, :source, 21, "
                    "'Pure needle leaved', 'accepted', 100.0, 100.0, "
                    "ST_GeomFromText('MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))', 3035), "
                    "'{}'::jsonb, repeat('c', 64), CAST(:run_id AS uuid), repeat('b', 64))"
                ),
                {
                    "site": sentinel_site,
                    "source": sentinel_source,
                    "run_id": str(uuid4()),
                },
            )
            before = connection.execute(
                text(
                    "SELECT package_version, source_attributes::text, record_fingerprint "
                    "FROM iris_staging.corine_land_cover_clc WHERE site_id = :site"
                ),
                {"site": sentinel_site},
            ).one()
        report = load_parse_result(
            _parse_result([_record(970000004, 970004)]),
            database_url,
            tmp_path,
            failure_after_inserts=1,
        )
        assert report["transaction_rolled_back"] is True
        with engine.connect() as connection:
            after = connection.execute(
                text(
                    "SELECT package_version, source_attributes::text, record_fingerprint "
                    "FROM iris_staging.corine_land_cover_clc WHERE site_id = :site"
                ),
                {"site": sentinel_site},
            ).one()
        assert after == before
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id IN (970003, 970004)"
                )
            )
        engine.dispose()


def test_conflict_is_rejected_and_rerun_after_failure_is_idempotent(database_url, tmp_path):
    engine = create_engine(database_url)
    first = _record(970000005, 970005)
    conflict = _record(970000005, 970005, extra="conflicting")
    try:
        conflict_report = load_parse_result(
            _parse_result([first, conflict]), database_url, tmp_path
        )
        assert conflict_report["outcome"] == "failed"
        assert conflict_report["transaction_rolled_back"] is False
        assert "identity conflict" in conflict_report["errors"][0]
        with engine.connect() as connection:
            assert _rows_for_identity(connection, 970005) == []

        records = [_record(970000006, 970006), _record(970000007, 970007)]
        failed = load_parse_result(
            _parse_result(records), database_url, tmp_path, failure_after_inserts=1
        )
        assert failed["counts"]["rolled_back"] == 1
        succeeded = load_parse_result(_parse_result(records), database_url, tmp_path)
        rerun = load_parse_result(_parse_result(records), database_url, tmp_path)
        assert succeeded["outcome"] == "success"
        assert succeeded["counts"]["inserted"] == 2
        assert rerun["outcome"] == "success"
        assert rerun["counts"]["inserted"] == 0
        assert rerun["counts"]["unchanged"] == 2
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id IN (970006, 970007)"
                )
            ).scalar_one() == 2
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id IN (970005, 970006, 970007)"
                )
            )
        engine.dispose()


def test_chunked_batches_aggregate_and_rerun_idempotently(database_url, tmp_path):
    engine = create_engine(database_url)
    site_id = 970008
    records = [_record(970000100 + index, site_id) for index in range(5)]
    batches = [
        _parse_result(records[0:2]),
        _parse_result(records[2:4]),
        _parse_result(records[4:5]),
    ]
    arguments = {
        "source_feature_count": 5,
        "input_file": "chunked.gpkg",
        "input_sha256": "a" * 64,
        "source_layer": "synthetic",
        "database_url": database_url,
        "report_dir": tmp_path,
        "batch_size": 2,
    }
    try:
        first = load_parse_batches(batches, **arguments)
        rerun = load_parse_batches(batches, **arguments)

        assert first["outcome"] == "success"
        assert first["batches_read"] == 3
        assert first["batches_committed"] == 3
        assert first["counts"]["features_read"] == 5
        assert first["counts"]["accepted"] == 5
        assert first["counts"]["inserted"] == 5
        assert first["counts"]["unchanged"] == 0
        assert first["counts"]["rejected"] == 0
        assert rerun["outcome"] == "success"
        assert rerun["counts"]["inserted"] == 0
        assert rerun["counts"]["unchanged"] == 5
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id = :site"
                ),
                {"site": site_id},
            ).scalar_one() == 5
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id = :site"
                ),
                {"site": site_id},
            )
        engine.dispose()


def test_chunked_intermediate_failure_commits_prior_batches_and_recovers(
    database_url, tmp_path
):
    engine = create_engine(database_url)
    site_id = 970009
    records = [_record(970000200 + index, site_id) for index in range(6)]
    batches = [
        _parse_result(records[0:2]),
        _parse_result(records[2:4]),
        _parse_result(records[4:6]),
    ]
    arguments = {
        "source_feature_count": 6,
        "input_file": "chunked-failure.gpkg",
        "input_sha256": "a" * 64,
        "source_layer": "synthetic",
        "database_url": database_url,
        "report_dir": tmp_path,
        "batch_size": 2,
    }
    try:
        failed = load_parse_batches(
            batches, failure_batch_number=2, **arguments
        )
        assert failed["outcome"] == "failed"
        assert failed["partial_commits"] is True
        assert failed["batches_committed"] == 1
        assert failed["counts"]["inserted"] == 2
        assert failed["counts"]["rolled_back"] == 1
        assert "injected failure in batch 2" in failed["errors"][0]
        assert (tmp_path / f"{failed['run_id']}.json").is_file()

        recovered = load_parse_batches(batches, **arguments)
        assert recovered["outcome"] == "success"
        assert recovered["counts"]["unchanged"] == 2
        assert recovered["counts"]["inserted"] == 4
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id = :site"
                ),
                {"site": site_id},
            ).scalar_one() == 6
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM iris_staging.corine_land_cover_clc "
                    "WHERE site_id = :site"
                ),
                {"site": site_id},
            )
        engine.dispose()
