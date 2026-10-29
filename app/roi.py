from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from pyproj import CRS, Transformer
from shapely import wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from sqlalchemy import text

from app.clc_contract import CLC_VECTOR_CLASS_LABELS


ANALYSIS_CRS = CRS.from_epsg(3035)
ANALYSIS_EPSG = 3035
DEFAULT_TABLE_REFERENCE = "iris_staging.corine_land_cover_clc"
TABLE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


@dataclass(frozen=True)
class ValidatedRoi:
    input_crs: str
    analysis_geometry: BaseGeometry
    analysis_wkt: str
    area_m2: float


def validate_roi(roi_wkt: str, roi_crs: str) -> ValidatedRoi:
    if not roi_crs or not roi_crs.strip():
        raise ValueError("ROI CRS is required")
    try:
        input_crs = CRS.from_user_input(roi_crs)
    except Exception as exc:
        raise ValueError(f"invalid ROI CRS: {roi_crs}") from exc

    try:
        geometry = wkt.loads(roi_wkt)
    except Exception as exc:
        raise ValueError("ROI WKT is invalid") from exc

    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"ROI must be Polygon or MultiPolygon, got {geometry.geom_type}")
    if geometry.is_empty:
        raise ValueError("ROI must not be empty")
    if not geometry.is_valid:
        raise ValueError("ROI is topologically invalid; no repair was applied")

    if input_crs == ANALYSIS_CRS:
        analysis_geometry = geometry
    else:
        transformer = Transformer.from_crs(input_crs, ANALYSIS_CRS, always_xy=True)
        analysis_geometry = transform(transformer.transform, geometry)

    if analysis_geometry.is_empty or not analysis_geometry.is_valid:
        raise ValueError("transformed ROI is invalid; no repair was applied")
    area_m2 = float(analysis_geometry.area)
    if not math.isfinite(area_m2) or area_m2 <= 0:
        raise ValueError("transformed ROI area must be finite and positive")

    return ValidatedRoi(
        input_crs=input_crs.to_string(),
        analysis_geometry=analysis_geometry,
        analysis_wkt=analysis_geometry.wkt,
        area_m2=area_m2,
    )


def query_roi_metrics(
    connection: Any,
    roi: ValidatedRoi,
    source_product: str,
    package_version: str,
    table_reference: str = DEFAULT_TABLE_REFERENCE,
) -> dict[str, Any]:
    table = _safe_table_reference(table_reference)
    parameters = {
        "roi_wkt": roi.analysis_wkt,
        "source_product": source_product,
        "package_version": package_version,
    }
    common_cte = f"""
        WITH roi AS (
            SELECT ST_GeomFromText(:roi_wkt, {ANALYSIS_EPSG}) AS geometry
        ), candidates AS (
            SELECT
                c.lc_code18,
                c.class_label,
                ST_Intersection(c.geometry, roi.geometry) AS intersection_geometry
            FROM {table} AS c
            CROSS JOIN roi
            WHERE c.source_product = :source_product
              AND c.package_version = :package_version
              AND ST_Intersects(c.geometry, roi.geometry)
        ), positive AS (
            SELECT
                lc_code18,
                class_label,
                intersection_geometry,
                ST_Area(intersection_geometry) AS intersection_area_m2
            FROM candidates
            WHERE NOT ST_IsEmpty(intersection_geometry)
              AND ST_Area(intersection_geometry) > 0
        )
    """
    summary = connection.execute(
        text(
            common_cte
            + """
            SELECT
                ST_Area((SELECT geometry FROM roi)) AS roi_area_m2,
                (SELECT count(*) FROM candidates) AS candidate_count,
                count(*) AS positive_intersection_count,
                COALESCE(sum(intersection_area_m2), 0) AS summed_intersection_area_m2,
                COALESCE(
                    ST_Area(ST_UnaryUnion(ST_Collect(intersection_geometry))),
                    0
                ) AS covered_area_m2
            FROM positive
            """
        ),
        parameters,
    ).mappings().one()
    class_rows = connection.execute(
        text(
            common_cte
            + """
            SELECT lc_code18, class_label, sum(intersection_area_m2) AS area_m2
            FROM positive
            GROUP BY lc_code18, class_label
            ORDER BY lc_code18, class_label NULLS LAST
            """
        ),
        parameters,
    ).mappings()

    roi_area_m2 = float(summary["roi_area_m2"])
    covered_area_m2 = float(summary["covered_area_m2"])
    summed_intersection_area_m2 = float(summary["summed_intersection_area_m2"])
    uncovered_area_m2 = max(0.0, roi_area_m2 - covered_area_m2)
    classes: dict[int, dict[str, Any]] = {}
    for row in class_rows:
        code = int(row["lc_code18"])
        label = row["class_label"] or CLC_VECTOR_CLASS_LABELS.get(code)
        item = classes.setdefault(
            code,
            {
                "lc_code18": code,
                "class_label": label,
                "class_label_status": (
                    "stored" if row["class_label"] is not None
                    else "mapped_fallback" if label is not None
                    else "unknown"
                ),
                "intersection_area_m2": 0.0,
            },
        )
        item["intersection_area_m2"] += float(row["area_m2"])

    for item in classes.values():
        item["intersection_area_ha"] = item["intersection_area_m2"] / 10_000.0
        item["roi_percentage"] = (
            item["intersection_area_m2"] / roi_area_m2 * 100.0
            if roi_area_m2
            else 0.0
        )

    return {
        "source_product": source_product,
        "package_version": package_version,
        "input_crs": roi.input_crs,
        "analysis_crs": f"EPSG:{ANALYSIS_EPSG}",
        "roi_wkt_analysis_crs": roi.analysis_wkt,
        "roi_area_m2": roi_area_m2,
        "roi_area_ha": roi_area_m2 / 10_000.0,
        "candidate_count": int(summary["candidate_count"]),
        "positive_intersection_count": int(summary["positive_intersection_count"]),
        "classes": list(classes.values()),
        "summed_intersection_area_m2": summed_intersection_area_m2,
        "covered_area_m2": covered_area_m2,
        "covered_area_ha": covered_area_m2 / 10_000.0,
        "uncovered_area_m2": uncovered_area_m2,
        "uncovered_area_ha": uncovered_area_m2 / 10_000.0,
        "coverage_percentage": covered_area_m2 / roi_area_m2 * 100.0,
        "overlap_excess_m2": summed_intersection_area_m2 - covered_area_m2,
        "label_note": "Stored labels are preferred; otherwise the verified CLCplus Vector mapping is applied for documented codes. LC_code18 is always preserved.",
        "semantic_note": "Statistics describe assigned vector polygon classes for the 2018 reference product, not exact current ground cover. No sub-polygon cover fraction is estimated.",
    }


def _safe_table_reference(table_reference: str) -> str:
    if not TABLE_REFERENCE_PATTERN.fullmatch(table_reference):
        raise ValueError("unsafe table reference")
    return table_reference
