"""Read-only, municipality-scoped evidence for a saved all-crash HIN report.

Crash totals describe currently loaded records, not certified annual coverage.
The stored corridor results are not recomputed or reclassified as an FSI HIN.
"""

from datetime import date

from sqlalchemy import text


_PERIOD = """
    muni_id = :muni_id
    AND crash_date >= :start_date AND crash_date < :end_date
"""

# The schema permits duplicate HIN rows. Pick the latest stored result for each
# selected segment, and never multiply crash counts by joining those raw rows.
_SELECTED = """
    selected AS MATERIALIZED (
        SELECT DISTINCT ON (hin.segment_id)
            hin.*, road.length_miles, road.road_name
        FROM hin_segments AS hin
        JOIN road_segments AS road ON road.segment_id = hin.segment_id
        WHERE hin.analysis_id = :analysis_id
          AND hin.is_significant IS TRUE AND road.muni_id = :muni_id
        ORDER BY hin.segment_id, hin.hin_id DESC
    )
"""

_EQUITY_KEYS = (
    "known_miles", "high_vulnerability_miles", "unknown_miles",
    "known_segments", "unknown_segments",
)


def _assemble_evidence(start_year, end_year, annual, totals, corridors, locations):
    """Fill calendar gaps and derive shares without inventing zero denominators."""
    annual_by_year = {int(row["year"]): dict(row) for row in annual}
    annual_rows = []
    for year in range(start_year, end_year + 1):
        row = dict.fromkeys((
            "total_crashes", "fatal_crashes", "serious_injury_crashes",
            "injury_unknown_crashes", "ped_crashes", "bike_crashes", "bike_unknown",
        ), 0)
        row.update(year=year, total_killed=None, total_injured=None)
        row.update(annual_by_year.get(year, {}))
        annual_rows.append(row)

    network = dict(totals)
    equity = {key: network.pop(key, 0) for key in _EQUITY_KEYS}
    for key, numerator, denominator in (
        ("road_share_pct", "selected_miles", "eligible_road_miles"),
        ("crash_share_pct", "selected_crashes", "loaded_crashes"),
        ("assigned_share_pct", "selected_crashes", "assigned_crashes"),
    ):
        network[key] = (
            100.0 * network[numerator] / network[denominator]
            if network[denominator] else None
        )

    corridor_rows = []
    for item in corridors:
        row = dict(item)
        exposure = row["miles"] * (end_year - start_year + 1)
        row["rate"] = row["crashes"] / exposure if exposure else None
        corridor_rows.append(row)
    corridor_rows.sort(key=lambda row: (
        -row["crashes"], row["corridor_id"] is None,
        row["corridor_id"] if row["corridor_id"] is not None else row["segment_ids"][0],
    ))
    return {
        "annual": annual_rows, "network": network, "corridors": corridor_rows,
        "locations": [dict(row) for row in locations], "equity": equity,
    }


def collect_report_evidence(db, analysis):
    """Collect bounded aggregates for the saved analysis using four SELECTs.

    Annual and network casualties remain NULL if any contributing record lacks
    that count, including when there are no contributing records. Equity uses
    the saved percentile-derived boolean; NULL means no known classification.
    """
    params = {
        "muni_id": analysis.muni_id, "analysis_id": analysis.analysis_id,
        "start_date": date(analysis.start_year, 1, 1),
        "end_date": date(analysis.end_year + 1, 1, 1),
    }
    annual = db.execute(text(f"""
        SELECT EXTRACT(YEAR FROM crash_date)::integer AS year,
            COUNT(*)::integer AS total_crashes,
            COUNT(*) FILTER (WHERE severity = 'fatal')::integer AS fatal_crashes,
            COUNT(*) FILTER (WHERE severity = 'serious_injury')::integer
                AS serious_injury_crashes,
            COUNT(*) FILTER (WHERE severity = 'injury_unknown')::integer
                AS injury_unknown_crashes,
            CASE WHEN COUNT(total_killed) = COUNT(*)
                THEN SUM(total_killed)::integer END AS total_killed,
            CASE WHEN COUNT(total_injured) = COUNT(*)
                THEN SUM(total_injured)::integer END AS total_injured,
            COUNT(*) FILTER (WHERE ped_involved IS TRUE)::integer AS ped_crashes,
            COUNT(*) FILTER (WHERE bike_involved IS TRUE)::integer AS bike_crashes,
            COUNT(*) FILTER (WHERE bike_involved IS NULL)::integer AS bike_unknown
        FROM crashes WHERE {_PERIOD}
        GROUP BY EXTRACT(YEAR FROM crash_date) ORDER BY year
    """), params).mappings().all()

    totals = db.execute(text(f"""
        WITH {_SELECTED},
        scoped_crashes AS MATERIALIZED (
            SELECT crash.*,
                EXISTS (SELECT 1 FROM selected
                        WHERE selected.segment_id = crash.segment_id) AS selected,
                EXISTS (SELECT 1 FROM road_segments AS road
                        WHERE road.segment_id = crash.segment_id
                          AND road.muni_id = :muni_id) AS assigned
            FROM crashes AS crash WHERE {_PERIOD}
        ), roads AS (
            SELECT COALESCE(SUM(length_miles), 0) AS loaded_road_miles,
                COALESCE(SUM(length_miles) FILTER (WHERE length_miles >= .01), 0)
                    AS eligible_road_miles
            FROM road_segments WHERE muni_id = :muni_id
        ), network AS (
            SELECT COALESCE(SUM(length_miles), 0) AS selected_miles,
                COUNT(*)::integer AS selected_segments,
                COALESCE(SUM(length_miles) FILTER (
                    WHERE in_vulnerable_tract IS NOT NULL), 0) AS known_miles,
                COALESCE(SUM(length_miles) FILTER (
                    WHERE in_vulnerable_tract IS TRUE), 0) AS high_vulnerability_miles,
                COALESCE(SUM(length_miles) FILTER (
                    WHERE in_vulnerable_tract IS NULL), 0) AS unknown_miles,
                COUNT(*) FILTER (WHERE in_vulnerable_tract IS NOT NULL)::integer
                    AS known_segments,
                COUNT(*) FILTER (WHERE in_vulnerable_tract IS NULL)::integer
                    AS unknown_segments
            FROM selected
        ), crash_totals AS (
            SELECT COUNT(*)::integer AS loaded_crashes,
                COUNT(*) FILTER (WHERE assigned)::integer AS assigned_crashes,
                COUNT(*) FILTER (WHERE selected)::integer AS selected_crashes,
                COUNT(*) FILTER (WHERE selected AND severity = 'fatal')::integer
                    AS selected_fatal_crashes,
                COUNT(*) FILTER (WHERE selected AND severity = 'serious_injury')::integer
                    AS selected_serious_injury_crashes,
                CASE WHEN COUNT(total_killed) FILTER (WHERE selected)
                        = COUNT(*) FILTER (WHERE selected)
                    THEN SUM(total_killed) FILTER (WHERE selected)::integer
                    END AS selected_killed,
                CASE WHEN COUNT(total_injured) FILTER (WHERE selected)
                        = COUNT(*) FILTER (WHERE selected)
                    THEN SUM(total_injured) FILTER (WHERE selected)::integer
                    END AS selected_injured
            FROM scoped_crashes
        )
        SELECT roads.*, network.*, crash_totals.*
        FROM roads CROSS JOIN network CROSS JOIN crash_totals
    """), params).mappings().one()

    corridors = db.execute(text(f"""
        WITH {_SELECTED}, unknown_injuries AS (
            SELECT segment_id, COUNT(*)::integer AS crashes
            FROM crashes WHERE {_PERIOD} AND severity = 'injury_unknown'
            GROUP BY segment_id
        )
        SELECT selected.corridor_id,
            MIN(COALESCE(NULLIF(BTRIM(selected.corridor_name), ''),
                         NULLIF(BTRIM(selected.road_name), ''), 'Unnamed road')) AS name,
            COUNT(*)::integer AS segment_count,
            SUM(selected.length_miles) AS miles,
            COALESCE(SUM(selected.crash_count_total), 0)::integer AS crashes,
            COALESCE(SUM(selected.crash_count_fatal), 0)::integer AS fatal_crashes,
            COALESCE(SUM(selected.crash_count_serious_injury), 0)::integer
                AS serious_injury_crashes,
            COALESCE(SUM(unknown_injuries.crashes), 0)::integer AS injury_unknown_crashes,
            ARRAY_AGG(selected.segment_id ORDER BY selected.segment_id) AS segment_ids
        FROM selected LEFT JOIN unknown_injuries
            ON unknown_injuries.segment_id = selected.segment_id
        GROUP BY selected.corridor_id,
            CASE WHEN selected.corridor_id IS NULL THEN selected.segment_id END
    """), params).mappings().all()

    locations = db.execute(text(f"""
        SELECT COALESCE(NULLIF(BTRIM(geocode_quality), ''), 'unknown') AS method,
            COUNT(*)::integer AS count
        FROM crashes WHERE {_PERIOD}
        GROUP BY COALESCE(NULLIF(BTRIM(geocode_quality), ''), 'unknown')
        ORDER BY count DESC, method
    """), params).mappings().all()
    return _assemble_evidence(
        analysis.start_year, analysis.end_year, annual, totals, corridors, locations,
    )
