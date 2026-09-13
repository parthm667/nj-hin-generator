# NJDOT dashboard import through 2025

The public [NJDOT Crash Data Dashboard](https://njdot.aashtowaresafety.net/njdot-crash-data-dashboard#/) supplies a newer crash snapshot. This import selects 2019–2025 and excludes 2026 from the database. Existing 2017–2018 archive records remain available. Overlapping 2019–2022 incidents are reconciled with existing records before insertion.

## Acquire a complete source snapshot

From `backend/`:

```bash
python -u scripts/download_njdot_dashboard.py \
  --start-year 2019 --end-year 2025 \
  --cache-dir ../data/raw/njdot-dashboard \
  --report ../data/processed/njdot-dashboard-download-report.json
```

The downloader discovers the public entity token and Crash Data metric from the portal and public dashboard configuration. It uses the dashboard's public metric search, download, and download-status endpoints. It uses no logged-in credentials or restricted dataset API.

The verified CSV request follows the browser's 59-column order. Although the dashboard configuration lists `Time of Crash`, the working export omits it; the CSV supplies `Date & Time of Crash`. Downloading the public unfiltered snapshot and filtering it locally reproduces the working interface. The raw transfer can therefore contain 2026 records, but the published cache `Crash-2019-2025.csv` cannot. Requests for optional additional columns are separate from the default pipeline and must return the requested headers to pass validation; their availability is not assumed.

Before publishing the filtered cache, acquisition checks:

- Full export row count against the public unfiltered search count.
- Selected row count against a public search using the same 2019–2025 year filter.
- Required headers, consistent row widths, valid dates, year/date agreement, nonempty numeric crash IDs, and unique selected IDs.
- Transfer content length when supplied, then SHA-256 and byte length for both source and filtered CSV.

The only accepted signed download destination is the observed NJDOT export path on `numetric-cache-prod.s3-fips.us-west-2.amazonaws.com`. Redirects are disabled. Tokens and signed URLs are not written to manifests or error logs. Requests have bounded retries and timeouts; export generation has a configurable wait bound (`--max-wait`, default 1,800 seconds).

Downloads and filtered files are staged before atomic replacement. The last successful manifest and CSV survive a failed retry; failure details are written to a separate `*-last-attempt.json`. A valid matching cache is reused after verification. A changed public selected count or invalid cache triggers reacquisition. Use `--refresh-cache` to request a fresh snapshot when corrections may have occurred without changing row counts.

An existing full public export can be reused with `--input-csv /path/to/Crash.csv`. This still checks both live public counts and records the input checksum, original row count, excluded rows, and filtered checksum. It does not infer the original download time from filesystem timestamps. Direct downloads record their actual retrieval time in UTC.

## Review and apply the import

The importer defaults to a database dry run:

```bash
python -u scripts/ingest_njdot_dashboard.py \
  --input-csv ../data/raw/njdot-dashboard/Crash-2019-2025.csv \
  --report ../data/processed/njdot-dashboard-import-report.json \
  --allow-new-historical
```

Review the report, then add `--apply`. Supply `--expected-count` from the acquisition manifest's selected `csv.rows`, plus its source URL and retrieval time, when running an operational import. `--allow-new-historical` permits previously unloaded 2019–2022 records only after the importer's identity, date, municipality, duplicate, and location checks; it does not bypass reconciliation.

Source `id_cr` is a dashboard identifier, not the historical archive's composite identifier. Department case numbers and municipality identity require exact reconciliation. Distinct dashboard IDs can share a historical case key, so source uniqueness alone does not establish unique incidents. Reports distinguish source rows, rejected or ambiguous records, matched records, and inserted records. Source casualty and severity contradictions are retained as explicit uncertainty rather than fabricated detail. See the [NJDOT data dictionary](https://support.numetric.com/en/articles/16736158-njdot-data-dictionary) for field definitions.

## Production execution

`deploy/run-once/09-import-dashboard-through-2025.sh` acquires the source, verifies its manifest and checksum, and calls the importer with structured subprocess arguments. It passes `--apply --allow-new-historical`, a staging SQLite path, source metadata, and the validated selected count.

The cache is under `/srv/data/raw/njdot-dashboard`; acquisition and import reports and the staging database are under `/srv/data/processed`. The measured full export was about 1.12 GB, filtered CSV 0.91 GB, and staging SQLite database 7,257,714,688 bytes (7.26 GB). PostgreSQL updates and WAL also need working space. Before acquisition or staging writes, the wrapper logs free space on `/srv/data` and exits nonzero if less than **20 GiB** is available. This conservative guard accounts for the source, stage, and database workspace on the production volumes' shared backing filesystem. It does not delete files or stop the worker.

The job runs in the foreground. It does not stop the analysis worker or retry a busy database in a loop. The importer waits for its existing ownership locks and commits database changes atomically. A failure exits nonzero; the existing autoupdate timer can retry later. Only successful completion creates the run-once marker. Public logs should contain aggregate counts and validation outcomes, not source rows or credentials.

Source revisions make earlier analyses stale after updates. Run a fresh analysis to use the imported data; existing analyses are preserved. Loaded counts describe successfully located and reconciled records, not complete real-world crash occurrence or finalized annual coverage.
