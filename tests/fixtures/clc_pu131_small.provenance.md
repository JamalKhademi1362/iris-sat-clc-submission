# CLCplus PU131 fixed fixture provenance

## Source

- Product: CLCplus Backbone 2018 vector.
- Package: PU131, package version `V1_1`.
- Original source layer name: `V1_0`.
- Metadata edition: `01.00`.
- Source attribution: Copernicus/CLMS CLCplus Backbone metadata, DOI `10.2909/d45d5114-fb86-4265-9c5a-a7225a511f7c`, with the CLC-BB User Manual I3-0 supplied for this assignment.
- Local source file: `clcplus_pu131_sample2.gpkg`.
- Local source SHA-256: `8667099bc5d216413cab35310df5174141cde116dbe949464bcf57185bc9bbca`.
- Source layer: `clms_clcplus_vector_2018_pu131_03035_v1_1__clms_clcplus_vector_2018_pu131_03035_v1_0`.

The local source contains 85,854 features. It is a manually selected PU131 subset, not a complete PU131 or Germany release. Its original selection area is not fully reproducible from the available evidence.

## Deterministic extraction

The fixture was created from the source layer without clipping or geometry repair:

1. Read the source layer in its native CRS, EPSG:3035.
2. Construct the fixed EPSG:3035 bounding box `(4140000, 2440000, 4145000, 2445000)` using `(min_x, min_y, max_x, max_y)` metres.
3. Select complete original features whose original geometry satisfies `geometry.intersects(bounding_box)`.
4. Sort selected rows by original `ID` ascending with a stable mergesort.
5. Write all available source attributes and the original geometries to the fixture layer. The exported GeoPackage FID is not used as source identity; the preserved `ID` field remains the source identifier candidate.

This method selected features that may extend beyond the bounding box because complete geometries were retained. The resulting fixture envelope is therefore larger than the selection box.

## Fixture result

- File: `clc_pu131_small.gpkg`.
- Layer: `clc_pu131_small`.
- Size: 1,359,872 bytes.
- SHA-256: `5f7091f8d4d51c870f67aac8064f5a649b25dcfc898826cfc086ba9c163820db`.
- Features: 1,058.
- Bounds: `(4139407.479919, 2439377.022278, 4146198.546875, 2446282.005493)` in EPSG:3035.
- CRS: EPSG:3035.
- Geometry: 1,058 `MultiPolygon` features.
- Classes: `21: 2`, `22: 12`, `31: 805`, `32: 1`, `33: 49`, `40: 1`, `51: 39`, `52: 57`, `53: 83`, `60: 2`, `100: 7`.
- Reference years: `2018: 1,058`.
- Nulls in `ID`, `Site_id`, `LC_code18`, `Ref_year`, `Shape_Area`, and geometry: zero.
- Duplicate original `ID` rows and duplicate `(Site_id, ID)` rows: zero.

## Preservation and checks

The selected source rows and fixture have matching columns, original IDs, all checked non-geometry attributes, and exact geometry WKB equality after the GeoPackage round trip. No source feature was clipped.

Shapely/GeoPandas checks were available and run locally: invalid geometries `0`, empty geometries `0`, and non-positive calculated areas `0`. For each feature, `calculated_area_m2 = geometry.area` in the source CRS EPSG:3035, `absolute_difference_m2 = abs(calculated_area_m2 - Shape_Area)`, and `relative_difference = absolute_difference_m2 / abs(Shape_Area)` for non-zero `Shape_Area`. The candidate acceptance tolerance is absolute difference `<= 0.05 m2` and relative difference `<= 1e-4` (0.01%), pending confirmation of the source field's documented units. The observed maximum absolute difference was approximately `0.0000499208 m2`, and the observed maximum relative difference was approximately `9.26214e-09`; both were within the candidate tolerance. This is a local planar check in EPSG:3035, not a PostGIS topology or source-unit proof.

## Scope limitation

This fixed fixture is a compact deterministic demonstration artifact derived from the existing local sample. It does not represent the full official PU131 release, does not prove complete-PU coverage, and does not prove authenticated automated download, PostGIS ingestion, or production suitability.
