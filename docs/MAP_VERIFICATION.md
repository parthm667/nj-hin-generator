# PostGIS-to-map verification — September 10, 2026

## Result

The coordinate pipeline passes. The local map's original street mismatch was caused by synthetic road fixtures, not a longitude/latitude swap or incorrect map projection. A separate nearest-road assignment defect was found and included in Phase 2 repairs.

Follow-up: these initial browser checks explicitly selected 2022 and missed the form's unsupported 2017–2021 default. That separate coverage/status defect has now been fixed and tested against Avalon's actual default flow; see [HISTORICAL_DATA_VERIFICATION.md](HISTORICAL_DATA_VERIFICATION.md). Map projection correctness alone was not sufficient verification of that flow.

## Evidence before Phase 2 ingestion

| Boundary checked | Observed result |
| --- | --- |
| Stored geometry | All municipalities, roads, crashes and tracts valid, nonempty, 2D, SRID 4326, with expected geometry types |
| DB → API | Both analyses' 2,000 crash features and 38 significant HIN features exactly matched database `ST_AsGeoJSON` output |
| GeoJSON precision | Maximum conversion displacement below 0.0001 meters |
| API → Leaflet | Longitude/latitude converted once to Leaflet latitude/longitude for points and single/multipart lines |
| PostGIS → Web Mercator | Checked point and road agreed with Leaflet's formula within 1.9e-9 meters |
| Display → background tiles | Independently projected 668 visible points from their geographic coordinates and an actual XYZ tile URL; maximum SVG center difference 0.679 pixels |
| HIN → background tiles | Checked line endpoint differences 0.354 and 0.443 pixels |

The screen comparison used background tile zoom/X/Y and tile bounding rectangles, not Leaflet's own projection function, avoiding a circular test. Integer SVG pixel rounding explains the subpixel differences.

## Why the original map looked wrong

The local fixture database held two artificial datasets totaling 500 roads and 7,000 crash points. One set exactly matched seed-42 generation; the other matched checked-in sample JSON. Sample roads are random straight segments with random road names and are not expected to trace actual OpenStreetMap streets.

Analysis 1 was stale after the second sample load: its stored summary counted 500 crashes, while its live crash endpoint returned 1,000. Analysis 2 was current with 1,000 crashes and 27 significant HIN lines. Existing analyses should be recreated after data changes.

The snap operation assigns a road ID and distance but preserves the original crash point. Sample points were typically about 6 meters from their assigned road, with some roughly 37 meters away. Such offsets are not a coordinate-system defect.

## Separate defects found

- Degree-based nearest-neighbor ranking selected a road that was not geodesically nearest for two West Windsor sample crashes, by 1.06 and 1.18 meters. Metric radius filtering and exact metric ranking are required.
- Existing assignments were skipped even after network or distance-threshold changes. New analyses need deliberate reassignment and clearing of out-of-radius matches.
- Synthetic road lengths were estimated from degrees × 69, overstating actual geodesic lengths by about 12–14% on average. Official ingestion calculates length from geometry in meters.

See [DATA_PIPELINE.md](DATA_PIPELINE.md) for real source geometry, route/milepost-derived crash locations and retained methodology limitations. Successful rendering does not establish complete crash coverage or validate the statistical method.

## Real-data integration

The production CRA build was served locally against the containerized API and the separate `nj_hin_real` PostGIS database. No production host was changed.

| Check | Observed result |
| --- | --- |
| West Windsor, 2022, new analysis 1 | Completed; 833 crash SVG markers and 64 HIN paths, matching API feature counts |
| Real points versus independently projected background tiles | 706 checked visible points, maximum difference 0.695 pixels |
| Real HIN versus background tiles | 102 checked visible endpoints, maximum difference 0.641 pixels |
| West Windsor location provenance | 4 reported coordinates and 829 calibrated route/milepost locations; API exposes `geocode_quality` |
| Newark, 2022, new analysis 2 | Completed; 12,408 crash markers and 662 HIN paths |
| Actual frontend polling | Network responses observed pending → running → completed, separated by approximately 3.03 and 3.05 seconds; no manual reload |
| Newark click through rendered results | Approximately 10.43 seconds on this local machine, not a hosted performance guarantee |
| Statewide crash geometry | 219,509 valid, nonempty EPSG:4326 points; zero outside their assigned municipality |
| Real DB → API equality | All 13,241 crash and 726 HIN geometries across both analyses exactly match `ST_AsGeoJSON`; zero location-quality mismatches |
| Independent metric assignment check | All 13,241 crashes checked: zero nearest-road, 50-meter-radius, municipality or stored-distance mismatches |
| Database analysis duration | West Windsor approximately 0.69 seconds; Newark approximately 4.87 seconds, measured from created/updated timestamps |
| Analysis URL refresh | `/analysis/2` reloads successfully and renders all 13,070 crash/HIN SVG paths |
| Health and PDF transport smoke test | `/health` returns healthy; analysis 1 PDF returns HTTP 200, `application/pdf`, 7,361 bytes beginning `%PDF-1.4` (layout/methodology not revalidated) |
| Backend verification | 94 tests passed with PostGIS enabled, zero skipped; five existing dependency/datetime deprecation warnings |
| Frontend regression tests | Both polling and single/multipart coordinate-order tests passed |
| Production frontend build | `CI=true npm run build` passed under Node 22, with the local API URL explicitly configured |
| API image | `docker build -t nj-hin-api:phase2 backend` succeeded; Docker image inspection reports 142,710,205 bytes |
| Complete offline ingestion replay | Exit 0, all three stages and 21 crash archives succeeded; zero inserted crashes, 219,509 duplicates; unchanged road counts/ID aggregates and reference-path count |

Real source road lines visually follow the background streets. Source-network age, road generalization, historical mileposts and reported-coordinate uncertainty can still produce local offsets; the projection check does not prove surveying accuracy. Statistical HIN counts above describe current software output, not a validation of its Phase 3 methodology.
