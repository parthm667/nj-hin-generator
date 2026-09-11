# Historical years and coverage safeguards

User approved September 10, 2026: backfill all available 2017–2021 county archives, preserve 2022, make year selection reflect loaded records, block missing years including gaps, and show legacy empty/stale results as requiring a rerun. No deployment or commit.

Baseline: real database has 219,509 crashes, all in 2022; Avalon municipality 9 has 47. Analysis 3 is the misleading completed zero result for 2017–2021. Four analyses now exist and must be preserved. The original synthetic database is out of scope.

Preservation check: 2022 count 219509; MD5 of ordered crash_id/external_id/EWKB/geocode_quality aggregate = e383fea6397a8b76d1d901c55d4fc993. Snap assignments are deliberately excluded because new analyses can refresh them.

Work ownership:
- metric_analysis: backend coverage query, API validation and invalid-result guards, response fields, regression tests.
- frontend: loaded-year form, empty/gap/error states, stale/empty analysis display and regression tests.
- statewide_crashes: historical source cache, narrowly tested parser compatibility, ingestion tests. Parent owns real database ingestion.
- Parent: integration, production-build browser tests, data preservation/coverage checks, docs and independent review coordination.

API agreement: municipality coverage exposes sorted available_years, crash_counts_by_year and total_crashes based on actual positive records. Analysis data_status is ready/no_data/missing_years/stale plus explanatory data_message and available/missing years. These are usable-record checks, not proof of complete source coverage. Existing analyses are not overwritten; count changes require a rerun.

Source feasibility: all 105 official county/year ZIP URLs responded HTTP200/application-zip; approximately54.08MiB compressed. Ingestion is paused for a regression-tested repair of older unquoted multiline trailing fields; malformed/ambiguous rows remain rejected.

Verification to complete: tests red/green; all105archives accounted for; finalcounts/rejections byyear/municipality; baseline2022hash unchanged; Avalon2017–2021 nonempty fullbrowserflow; existinganalysis3 warning; empty/missing-year API rejects without creatinganalysis; alltest suites andCIbuild; independent review.

Parser and cache complete: 126 validated ZIPs (2017–2022), 54.08MiB historical compressed data. Narrow recovery for 49-column prefix plus bounded trailing damage-text continuations and two-column numeric badge ending; supports observed blank continuation and verified Cape May internal filename variants. Parent42focusedtests pass after atomic-report fix; independent parser review approved.

First completed loads:2017=183362 inserted,94372 rejected,21archives succeeded;2019=231413 inserted,51788 rejected,21archives succeeded. 2021 firstattempt inserted47148 then WindowsPermissionError replacingprogressmanifest stoppedprocess; originalreport retained at historical-2021/report.json. Added tested5attemptPermissionError-only retry(750msbackofftotal), leavingoldmanifestintact onpermanentfailure. Resumed2021 to historical-2021/retry-report.json;2018/2020alsoactive. Alljobs useexistingroadnetworkandindependentyearIDs; no roads or2022rowsreplaced.

FrontendauthorRED8newfailures/2existingpass, thenGREEN10tests. ParentfoundplaceholderNumber('')→0 edge, authoraddingtargetedregression/fix beforefinalbrowserbuild. Productionbundle needs REACT_APP_API_URL=http://127.0.0.1:58001/api.

Completed: all105historicalarchives loaded,2017finalreplayappliesblankcontinuationfixallcounties(additional40usable). Final2017/18/19/20/21counts183402/202248/231413/172614/200265;historic989942,totalwith2022=1209451distinctexternalIDs. All564munis haveatleastoneyear;530haveall5historicalyears. Geometriesvalid/nonempty4326andcovered,0bad.2022hash/countunchanged. FinalCapeMay5yearreplayinsert0/duplicate9392/reject2765. SourcePrincetonTWPunmatched4explicitlyretainedasrejections.

Parentverification: backend113passed7warnings,frontend11passed;correctAPIenvCIproductionbuildpassed;Dockerhistoricalimagebuiltandnewcontainer nj-hin-historical-api serving58001;oldphase2APIcontainerstoppedandpreserved. BrowserAvalondefault2017–21 completesnewanalysis5:133crashes27HIN160paths;reloadworks. Clear-yearhelperdisablesRun;Allendale2020gapblocksUIandPOST422,count5unchanged. Oldanalysis3nostats/PDF/map/green;showsstale. Independentmetriccheckall133crashes27HINexactDB/API,qualitymatched,0metricmismatches;PDF5HTTP2007335bytes. Correctiontopriorassumption:analysis4isWestWindsor2017–21stalezero,NOTAvalon2022;baseline2022analyses1/2remainready. Docsfinalreportcreated;independentfinaldocs/evidencereviewpending. No commit/deploy.

Final independent review approved code, data and documentation. Reviewer precision correction applied: the 40 extra2017records are1initialCapeMayreplay+39finalstatewidereplay, not40insertedbyfinalreplayalone. All requestedwork complete; records/analyses preserved, localpreviewavailable, no deployment/commit. Remaining source completeness and Phase3methodology limitations documented, not silently declaredfixed.
