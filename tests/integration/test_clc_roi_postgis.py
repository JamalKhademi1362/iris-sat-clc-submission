from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.roi import query_roi_metrics, validate_roi


load_dotenv(".env.clc.local")


@pytest.fixture
def synthetic_connection():
    database_url = os.getenv("CLC_DATABASE_URL")
    if not database_url:
        pytest.skip("CLC_DATABASE_URL is not configured")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TEMP TABLE synthetic_clc (
                    source_product text NOT NULL,
                    package_version text NOT NULL,
                    lc_code18 smallint NOT NULL,
                    class_label text,
                    geometry geometry(MULTIPOLYGON,3035) NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        rows = [
            ("product-a", "V1_1", 10, "Class A", "MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))"),
            ("product-a", "V1_1", 20, "Class B", "MULTIPOLYGON(((10 0,20 0,20 10,10 10,10 0)))"),
            ("product-a", "V1_1", 30, "Class C", "MULTIPOLYGON(((5 0,15 0,15 10,5 10,5 0)))"),
            ("product-a", "V1_1", 99, "Boundary only", "MULTIPOLYGON(((30 0,40 0,40 10,30 10,30 0)))"),
            ("product-a", "V2", 77, "Other version", "MULTIPOLYGON(((0 0,30 0,30 10,0 10,0 0)))"),
            ("product-b", "V1_1", 88, "Other product", "MULTIPOLYGON(((0 0,30 0,30 10,0 10,0 0)))"),
        ]
        connection.execute(
            text(
                """
                INSERT INTO synthetic_clc
                    (source_product, package_version, lc_code18, class_label, geometry)
                VALUES
                    (:product, :package, :code, :label, ST_GeomFromText(:wkt, 3035))
                """
            ),
            [
                {"product": p, "package": v, "code": c, "label": l, "wkt": w}
                for p, v, c, l, w in rows
            ],
        )
        yield connection
    engine.dispose()


def test_multiple_classes_partial_overlap_boundary_and_release_isolation(synthetic_connection):
    roi = validate_roi(
        "POLYGON((0 0,30 0,30 10,0 10,0 0))",
        "EPSG:3035",
    )
    result = query_roi_metrics(
        synthetic_connection,
        roi,
        source_product="product-a",
        package_version="V1_1",
        table_reference="pg_temp.synthetic_clc",
    )

    assert result["candidate_count"] == 4
    assert result["positive_intersection_count"] == 3
    assert result["roi_area_m2"] == pytest.approx(300.0)
    assert result["covered_area_m2"] == pytest.approx(200.0)
    assert result["uncovered_area_m2"] == pytest.approx(100.0)
    assert result["coverage_percentage"] == pytest.approx(200 / 3)
    assert result["summed_intersection_area_m2"] == pytest.approx(300.0)
    assert result["overlap_excess_m2"] == pytest.approx(100.0)
    assert {item["lc_code18"] for item in result["classes"]} == {10, 20, 30}
    assert all(item["intersection_area_m2"] == pytest.approx(100.0) for item in result["classes"])


def test_no_intersection_returns_uncovered_roi(synthetic_connection):
    roi = validate_roi("POLYGON((100 100,110 100,110 110,100 110,100 100))", "EPSG:3035")
    result = query_roi_metrics(
        synthetic_connection,
        roi,
        source_product="product-a",
        package_version="V1_1",
        table_reference="pg_temp.synthetic_clc",
    )
    assert result["candidate_count"] == 0
    assert result["positive_intersection_count"] == 0
    assert result["covered_area_m2"] == 0
    assert result["uncovered_area_m2"] == pytest.approx(100.0)
    assert result["coverage_percentage"] == 0
    assert result["classes"] == []


def test_invalid_roi_is_rejected_without_repair():
    with pytest.raises(ValueError, match="topologically invalid"):
        validate_roi("POLYGON((0 0,10 10,0 10,10 0,0 0))", "EPSG:3035")


@pytest.mark.parametrize("roi_crs", ["", "not-a-crs"])
def test_missing_or_malformed_roi_crs_is_rejected(roi_crs):
    with pytest.raises(ValueError, match="(ROI CRS is required|invalid ROI CRS)"):
        validate_roi("POLYGON((0 0,1 0,1 1,0 1,0 0))", roi_crs)


def test_unsupported_roi_geometry_is_rejected():
    with pytest.raises(ValueError, match="must be Polygon or MultiPolygon"):
        validate_roi("POINT(0 0)", "EPSG:3035")


def test_multipolygon_roi_is_transformed_to_analysis_crs():
    roi = validate_roi(
        "MULTIPOLYGON(((-3 50,-2.99 50,-2.99 50.01,-3 50.01,-3 50)))",
        "EPSG:4326",
    )
    assert roi.input_crs == "EPSG:4326"
    assert roi.area_m2 > 0
    assert roi.analysis_geometry.geom_type == "MultiPolygon"
