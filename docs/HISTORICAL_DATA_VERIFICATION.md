# Historical-year fix and verification — September 10, 2026

## Root cause and change

The first real-data load contained only 2022, but the form defaulted to 2017–2021. The backend treated an empty input period as a successfully completed analysis. Earlier map verification explicitly selected 2022 and missed that default user flow.

The fix backfills the available 2017–2021 county archives and checks actual loaded years before starting an analysis. A municipality must have at least one usable loaded record in every selected year. This is an availability guard, not a claim that the source is complete or that an absent record means no crash occurred.

The form now uses loaded-year selects, prefers 2017–2021 when available, otherwise chooses the latest contiguous range of up to five years, and blocks empty/gapped selections. Existing empty, incomplete and count-stale analyses show a warning and a link to create a new analysis. Stored analyses are preserved. Their map/summary/export endpoints reject invalid results with HTTP 409; missing-year creation requests return HTTP 422 before inserting an analysis.

This document records the earlier historical backfill verification, not the final methodology. The subsequent [correctness and safeguards work](CORRECTNESS_AND_SAFEGUARDS.md) replaced count-only freshness with database revision triggers and a method/configuration fingerprint. Same-count source edits and road/SVI/boundary changes now invalidate prior results. The earlier analysis IDs and 27-segment result below are historical evidence; they must be rerun under the corrected count-based method.

## Source compatibility

All 105 historical county/year ZIP URLs responded successfully and their downloaded archives passed validation. The local historical cache is approximately 54.08 MiB compressed. Some 2017–2018 records split the trailing property-damage text field across physical lines; a bounded parser now recovers only the verified 49-column prefix/continuation/numeric-badge pattern. Ambiguous or incomplete sequences remain explicit rejections. Cape May's older internal filenames contain a space; only the verified county/year variants are accepted.

On Windows, a progress-report replacement hit a transient file lock after 47,148 rows of 2021 were committed. The writer now retries only `PermissionError`, with five attempts and 750 ms total backoff, then re-raises. The old report is preserved on permanent failure. The resumed 2021 load recognized those committed rows as duplicates instead of inserting them again.

## User-flow verification

- Avalon coverage reports 10 / 6 / 25 / 40 / 52 loaded crashes for 2017 / 2018 / 2019 / 2020 / 2021, plus 47 for 2022.
- Selecting Avalon in the real production build defaults to 2017–2021 and enables Run Analysis. New local analysis 5 completes without refreshing: **133 crash points and 27 HIN segments**, rendered as 160 SVG paths. Refreshing `/analysis/5` also succeeds.
- Clearing the start year disables submission and displays “Choose a start and end year.” It no longer converts the empty selection to year zero.
- Existing Avalon analysis 3 remains stored with its original zero summary, but the UI shows “Results out of date.” There is no green Completed label, statistics panel, PDF control or map request for that invalid result.
- Allendale has no loaded 2020 records. Its form defaults to 2021–2022; selecting 2017–2021 disables Run and identifies 2020 as missing. A direct API request returns 422 with `missing_years: [2020]`; analysis count remains five before and after that rejected request.

Local screenshot evidence: `.playwright-mcp/avalon-2017-2021.png`. This is a local verification artifact, not a hosted deployment.

The read-only database/API comparison matched all 133 Avalon crash IDs, geometries and location-quality values, plus all 27 HIN IDs/geometries. Nearest-road, 50-meter radius and stored-distance checks found zero mismatches; the maximum assigned distance was 0.893 meters. Location provenance is one reported-coordinate point and 132 calibrated route/milepost points. PDF transport returned HTTP 200, `application/pdf`, 7,335 bytes starting `%PDF-1.4`; this does not revalidate report methodology or layout.

At the time of this historical-backfill verification, analysis 3's crash, HIN, summary, PDF and alternate GeoJSON endpoints returned 409. Analysis 4 was another stored but stale empty result, for West Windsor 2017–2021. Pre-backfill 2022 analyses 1 and 2 were ready at that stage; the subsequent input-version and methodology upgrade now marks these unversioned results stale too, requiring a rerun.

## Final loaded data

| Year | Loaded crashes | Municipalities with records | Counties with records |
| --- | ---: | ---: | ---: |
| 2017 | 183,402 | 547 | 21 |
| 2018 | 202,248 | 546 | 21 |
| 2019 | 231,413 | 555 | 21 |
| 2020 | 172,614 | 552 | 21 |
| 2021 | 200,265 | 555 | 21 |
| 2022, preserved | 219,509 | 552 | 21 |

The backfill added **989,942 historical crashes**. The database now contains **1,209,451 crashes with 1,209,451 distinct external IDs**, covering all 564 municipalities in at least one loaded year. Only 530 municipalities have positive loaded records in every year of 2017–2021; do not infer complete five-year coverage for the rest. All stored crash geometries are valid, nonempty EPSG:4326 and covered by their assigned municipality.

The historical reports account for 1,267,391 parsed records/rejection units: 989,942 usable crashes and 277,449 rejections. Rejections comprise 213,259 missing route references, 64,177 unresolved route measures, eight malformed record sequences, four unmatched municipality labels and one ambiguous route location. Multiline recovery combines physical lines into logical records, so these are not raw line counts. The four unmatched labels are Mercer `PRINCETON TWP` records (two each in 2017 and 2018); current boundaries represent consolidated Princeton. No silent boundary alias was added.

Historical location methods are 329,281 reported-coordinate points and 660,661 route/milepost-derived points. Current route geometry is used to locate historical mileposts, so network-age uncertainty and the earlier injury/bicycle limitations remain relevant.

The parser repair and follow-up loads recovered 40 additional usable 2017 records relative to the first pass: one during the initial Cape May replay and 39 during the final statewide replay. That statewide replay used the completed blank-continuation repair for every county. The authoritative local reports are:

- `data/processed/historical-2017/replay-report.json`
- `data/processed/historical-2018/report.json`
- `data/processed/historical-2019/report.json`
- `data/processed/historical-2020/report.json`
- `data/processed/historical-2021/retry-report.json`

Every authoritative report marks all 21 county archives succeeded. The original interrupted 2021 report remains separately preserved. The final Cape May replay across all five historical years inserted zero rows, recognized 9,392 duplicates and reproduced 2,765 rejections (`data/processed/historical-cape-may-repeat/final-report.json`). The statewide 2022 count and ordered identity/geometry/location-quality hash stayed unchanged at 219,509 and `e383fea6397a8b76d1d901c55d4fc993`.

The local database occupied approximately 1,116 MB during verification, including indexes and prior ingestion activity. Allow headroom; this observation is not a minimum clean-load size.

## Regression checks

Test-first failures reproduced the unsupported-year, stale/empty-success, export-bypass, older-file-format and Windows-report-lock bugs. Final checks: 113 backend tests passed with PostGIS enabled (seven existing dependency/datetime deprecation warnings); 11 frontend tests passed; the Node 22 `CI=true` production build and API Docker build passed. Independent code review found no remaining blockers in the parser, bounded report retry or frontend/backend coverage contract.

No production deployment, commit, replacement of the road network or deletion of prior analyses was performed.
