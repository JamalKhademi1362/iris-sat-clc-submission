# Fixed CLC ROI example provenance

- Input CRS: EPSG:3035.
- Analysis CRS: EPSG:3035.
- ROI geometry: `POLYGON((4140000 2441000,4142000 2441000,4142000 2443000,4140000 2443000,4140000 2441000))`.
- Selection box context: EPSG:3035 `(4140000,2440000,4145000,2445000)`.

The ROI is a 2,000 m by 2,000 m rectangle fully inside the fixture selection box. It was selected from the loaded fixture's actual geometry distribution because it intersects multiple source classes, has partial coverage, and remains compact enough for a reproducible example. It is not assumed to have complete CLC coverage merely because it lies inside the selection box.

The query scopes to product `CLCplus Backbone 2018` and package `V1_1`, uses `ST_Intersects` for candidates, computes `ST_Intersection` areas in EPSG:3035, excludes zero-area boundary-only contacts, and reports both summed intersection area and the union area. Stored class labels are used when present; otherwise the verified CLCplus Backbone 2018 code mapping is applied for documented codes. `LC_code18` is always preserved as the source classification code.

The result describes assigned vector polygon classes for the 2018 reference product. It is not an estimate of exact current ground cover and does not estimate sub-polygon cover fractions.
