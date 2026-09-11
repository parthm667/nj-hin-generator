# Official NJ data pipeline

## Sources and map coordinates

Municipalities come from the [NJGIN Municipal Boundaries layer](https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/ArcGIS/rest/services/NJ_Municipal_Boundaries_3424/FeatureServer/0), using `MUN_CODE`, `MUN_LABEL`, and `COUNTY`. The layer returned 564 features during the September 10, 2026 source check. A source count is not a guarantee that every municipality has usable crash records.

Roads come from the [NJDOT Roads Functional Class layer](https://services.arcgis.com/HggmsDF7UJsNN1FK/arcgis/rest/services/NJDOT_Roads_Functional_Class/FeatureServer/6). It returned 105,977 source features, with SRI route identifiers, functional classes, milepost limits and calibrated vertex measures. The official source covers public roads; the OpenStreetMap background is a separate cartographic layer, not the analysis network. Small differences between their geometry and update dates are expected.

Analysis geometries and GeoJSON use EPSG:4326 longitude/latitude. Leaflet converts these to its EPSG:3857 background display. The [PostGIS GeoJSON guidance](https://postgis.net/docs/ST_AsGeoJSON.html) and [Leaflet reference](https://leafletjs.com/reference.html#geojson-coordstolatlng) describe these conventions. Do not change stored coordinates to Web Mercator merely to match the background.

The source road loader requests Esri JSON with `returnM=true` and `outSR=4326`: ordinary GeoJSON drops the calibrated M values. Raw reference paths are kept separately from the short, two-dimensional analysis segments. [ST_LocateAlong](https://postgis.net/docs/ST_LocateAlong.html) locates a route milepost using the actual vertex measures; assigning linearly interpolated measures only at feature endpoints would discard that calibration.

## Crash location and coverage limitations

NJDOT Accidents archives are county/year files. The initial schema-valid inspection of 2022 Mercer counted 9,551 rows: 1,558 had latitude/longitude and 8,950 had SRI/milepost. Full ingestion counted 9,558 raw records, including malformed rows, and loaded 8,596 usable crashes. Availability of a route reference does not itself guarantee a usable or unambiguous match. Missing-coordinate records must not be silently treated as absent crashes.

Prefer usable reported coordinates. Route/milepost-derived locations are a separate quality category and use the current downloaded route network, which can differ from the historical crash-year network. Ambiguous routes, missing measures, unmatched municipalities, invalid dates/severity and unavailable source files must be counted and reported. Do not substitute a municipal centroid for an unresolved crash.

The Accidents table distinguishes fatal, injury and property-damage crashes; it does not distinguish serious from minor injury. The current application stores undifferentiated injuries in `minor_injury`. Bicycle involvement is not available in this table, and pedestrian flags based on killed/injured counts do not capture all uninjured pedestrians. A bicycle HIN cannot be inferred from these records alone.

All 21 county Accidents archive URLs were available for each year 2017–2022 during the source checks. Tested equivalent 2023 and 2024 URLs returned 404; that finding concerns the specific download URLs tested, not exhaustive availability across every county or a claim that no newer crash data exists elsewhere. The first verified load targeted 2022; the historical backfill subsequently added 2017–2021. Additional years require explicit selection and successful source checks.

Crash-to-road assignment preserves the stored crash location. It records a segment and meter distance; it does not visually move the point onto the line. Reported points can therefore appear beside a road. Route-derived points already lie on their reference geometry.

## Operational boundaries

Use a clean real-data database, separate from generated sample fixtures. The fixture roads are artificial straight lines with randomized names and are not expected to follow the background streets.

Initialize PostGIS and schema explicitly, then load boundaries, roads and crashes in that order. Run ingestion on a separate machine or one-off job, never inside API startup. Re-run analyses after data/network changes: existing HIN results are snapshots, while the crash endpoint reads the current municipality/year records.

From `backend/`, in an activated Python 3.11/3.12 environment:

```bash
python -m pip install -r requirements-ingest.txt
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/REAL_DATABASE'
python scripts/init_schema.py
python scripts/ingest_all_real_data.py --start-year 2017 --end-year 2022 --allow-partial-roads \
  --cache-dir ../data/raw/official \
  --report ../data/processed/real-data-manifest.json
```

PowerShell uses `$env:DATABASE_URL = 'postgresql://...'` and a single-line command instead of Bash's continuation backslash. Never use production credentials for tests. No production deployment has been performed as part of this local verification.

The orchestrator loads all boundaries and roads before crashes. Omit `--county` for all 21 counties, or repeat it to select counties (for example `--county Mercer --county Middlesex`). Use `--skip-municipalities --skip-roads` when those stages are already loaded and unchanged. `--offline` requires cached source files; `--refresh-cache` downloads again. Failures must produce a nonzero exit status; read the stage reports referenced by the manifest, including rejection and location-method counts.

Road ingestion is strict by default. The example explicitly opts into the checked source's partial coverage: nine features have no geometry and five otherwise usable XY paths lack valid calibrated measures. `--allow-partial-roads` allows these reported record-level omissions after complete pagination and a nonempty analysis-network load; it does not suppress schema, download or pagination failures. Inspect `analysis_coverage_complete`, `reference_coverage_complete`, and `partial_coverage_accepted` in the road report. Remove the flag to require zero reported omissions. A strict failure can occur after valid road rows have committed, so inspect the report before rerunning.

The official pipeline needs only `requirements-ingest.txt`, not GDAL or the legacy GeoPandas/Fiona stack in `requirements-scripts.txt`. The API image still installs only `requirements.txt`.

After loading, check actual coverage rather than just total rows:

```sql
SELECT m.county, EXTRACT(YEAR FROM c.crash_date)::int AS year,
       count(*) AS loaded_crashes,
       count(DISTINCT c.muni_id) AS municipalities_with_crashes
FROM crashes c JOIN municipalities m USING (muni_id)
GROUP BY m.county, year ORDER BY year, m.county;

SELECT geocode_quality, count(*) FROM crashes GROUP BY geocode_quality;

SELECT count(*) FILTER (WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom)) AS bad_geometry,
       count(*) FILTER (WHERE ST_SRID(geom) <> 4326) AS bad_srid
FROM crashes;
```

The frontend fetches each municipality's loaded years before enabling analysis. It defaults to 2017–2021 when all five years have usable records; otherwise it selects the latest contiguous range of up to five available years. Empty ranges and gaps are blocked in both the form and API. Availability means positive loaded-record counts, not proof that every actual crash is represented.

The API exposes this information at `/api/municipalities/{id}/coverage`. It rejects a missing-year analysis with HTTP 422 before creating a record. Existing completed analyses whose selected-year counts no longer match the database are marked with a separate `data_status`; stale, empty and missing-year results cannot be exported or fetched as current map/summary results (HTTP 409). The UI asks the user to run a new analysis instead of showing green completion and zero totals. Stored analyses are preserved, not silently recalculated.

Freshness is count-based: it detects this backfill but cannot detect arbitrary same-count edits to coordinates, severity or the road network. Recreate analyses after any source/network change even if the record count is unchanged. An empty HIN with nonzero crash input can be legitimate and is not treated as missing crash data.

## Initial 2022-only verification — September 10, 2026

For the current 2017–2022 totals and the corrected default-year user flow, see [HISTORICAL_DATA_VERIFICATION.md](HISTORICAL_DATA_VERIFICATION.md). The following preserves the evidence from the initial 2022-only load.

The initial separate real-data database contained 564 municipality boundaries, 460,639 analysis road segments covering all 564 municipalities, and 105,968 calibrated reference paths. Its first load added 219,509 crashes for 2022 across all 21 counties and 552 municipalities. These figures describe that first load, not the subsequent historical backfill or complete crash coverage for every municipality.

Of 242,599 raw crash records, 23,090 were rejected: 12,267 lacked a matching route reference, 10,772 could not resolve a usable route measure, and 51 had an unexpected column count. Loaded locations comprise 86,936 reported-coordinate points and 132,573 route/milepost-derived points. All loaded points are valid, nonempty EPSG:4326 geometries covered by their assigned municipality. Source and derived-location uncertainty still applies.

The 12 municipalities with no loaded 2022 crashes are Margate City, Cresskill Borough, Park Ridge Borough, Beverly City, Cape May Point Borough, Newfield Borough, Wenonah Borough, West Long Branch Borough, Pine Beach Borough, Haledon Borough, Bernardsville Borough and Winfield Township. Do not interpret this absence as zero actual crashes.

See [MAP_VERIFICATION.md](MAP_VERIFICATION.md) for actual browser and API checks. Machine-readable local reports are under `data/processed/`; source caches and reports are deliberately not committed. Retain or export them with any production ingestion job.

Allow database storage headroom for source routes, indexes and ingestion updates. This local database occupied approximately 712 MB during repeat-load verification, including retained row versions and earlier load activity; that is an operational observation, not a minimum clean-load size. Do not assume a 500 MB database allowance will accommodate this workflow or additional years.

The full offline replay completed successfully with `--allow-partial-roads`: all three stages and all 21 crash archives succeeded. Boundaries inserted zero rows; roads remained at 460,639 with unchanged ID count/minimum/maximum/sum; reference paths remained at 105,968. Crash replay inserted zero rows, recognized all 219,509 existing external IDs as duplicates, and reproduced the same 23,090 rejections and location-method totals. Its manifest is `data/processed/phase2-repeat/manifest.json`.

## Corrected screening and remaining source limitations

Significance now uses actual crash counts and a leave-one-out road-class baseline, with municipality fallback when no class reference exists. Severity weights are descriptive only. Same-route segments form separate corridors unless their endpoints connect within one meter. This remains exploratory screening: traffic exposure, spatial dependence, over-dispersion, and multiple testing need professional validation. No grant-readiness certification is implied.

Fragments shorter than 0.01 mile remain stored but are excluded from screening and baseline exposure. This is an explicit protective threshold, not an independently validated safety standard.

Historical casualty enrichment uses `python scripts/ingest_njdot_crashes.py --start-year 2017 --end-year 2022 --offline --enrich-only`. It updates existing source IDs only and does not move crash points. Official Accidents severity I is `injury_unknown`, not minor injury; bicycle involvement is NULL because the source cannot establish it. Person counts are distinct from fatal/injury crash counts. See `data/processed/casualty-enrichment-report.json` for the local run evidence.

Load official CDC/ATSDR SVI using `python scripts/ingest_svi.py`. The selected snapshot is 2020 U.S. national tract rankings, not a custom ACS proxy and not a demographic measurement for every crash year. `RPL_THEMES` is converted from 0–1 to the application's 0–100 scale. Official missing scores remain NULL. The local load contains 2,175 NJ tracts, including 10 without ranks. Source metadata and checks are retained in `data/processed/svi-2020-report.json`.

Global dataset revisions invalidate old analyses after source changes, even same-count edits. The method version is also checked. Routine derived crash snapping does not alter source revisions. All old unversioned results require a rerun; they are not deleted. Positive loaded counts do not certify source completeness: hundreds of thousands of source records still lack usable locations and are excluded rather than fabricated.
