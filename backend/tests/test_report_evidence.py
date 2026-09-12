from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from test_metric_analysis import (
    count_statements, insert_analysis, insert_crash, insert_municipality,
    insert_road, metric_db,
)


def test_annual_gaps_remain_visible_and_casualty_unknown_is_preserved():
    from app.services.report_evidence import _assemble_evidence

    result = _assemble_evidence(
        2022, 2024,
        [{"year": 2023, "total_crashes": 2, "total_killed": None,
          "total_injured": 3}],
        {"eligible_road_miles": 0, "selected_miles": 0,
         "loaded_crashes": 0, "selected_crashes": 0, "assigned_crashes": 0},
        [], [],
    )
    assert [row["year"] for row in result["annual"]] == [2022, 2023, 2024]
    assert result["annual"][0]["total_crashes"] == 0
    assert result["annual"][0]["total_killed"] is None
    assert result["annual"][0]["total_injured"] is None
    assert result["annual"][1]["total_injured"] == 3
    assert result["annual"][1]["total_killed"] is None
    assert result["network"]["road_share_pct"] is None
    assert result["network"]["crash_share_pct"] is None
    assert result["network"]["assigned_share_pct"] is None


def test_network_denominators_and_corridor_rates_use_selected_period():
    from app.services.report_evidence import _assemble_evidence

    result = _assemble_evidence(
        2022, 2024, [],
        {"loaded_road_miles": 12, "eligible_road_miles": 10,
         "selected_miles": 2, "loaded_crashes": 100,
         "selected_crashes": 20, "assigned_crashes": 40,
         "known_miles": 1, "unknown_miles": 1,
         "high_vulnerability_miles": 0, "known_segments": 1,
         "unknown_segments": 1},
        [{"corridor_id": 4, "segment_ids": [4], "miles": 2, "crashes": 12},
         {"corridor_id": None, "segment_ids": [9], "miles": 0, "crashes": 2},
         {"corridor_id": 2, "segment_ids": [2, 3], "miles": 1, "crashes": 12}],
        [{"method": "unknown", "count": 5}],
    )
    assert result["network"]["road_share_pct"] == 20
    assert result["network"]["crash_share_pct"] == 20
    assert result["network"]["assigned_share_pct"] == 50
    assert [row["corridor_id"] for row in result["corridors"]] == [2, 4, None]
    assert [row["rate"] for row in result["corridors"]] == [4, 2, None]
    assert result["equity"]["unknown_miles"] == 1
    assert result["equity"]["known_miles"] == 1
    assert "unknown_miles" not in result["network"]


def test_collector_scopes_sources_and_deduplicates_selected_segments(metric_db):
    from app.services.report_evidence import collect_report_evidence

    db, conn, _ = metric_db
    insert_municipality(conn, -97101)
    insert_municipality(conn, -97102)
    insert_analysis(conn, -97101, -97101, start_year=2023, end_year=2024)
    insert_analysis(conn, -97102, -97101, start_year=2023, end_year=2024)
    for segment, muni, length in [(-97101, -97101, 1), (-97102, -97101, 2),
                                  (-97103, -97101, .005), (-97104, -97102, 5)]:
        insert_road(conn, segment, muni, "LINESTRING(-74.5 40.5,-74.4 40.5)",
                    length_miles=length)
    conn.execute(text("""
        INSERT INTO hin_segments
            (analysis_id, segment_id, severity_score, crash_rate, is_significant,
             corridor_id, crash_count_total, crash_count_fatal,
             crash_count_serious_injury, in_vulnerable_tract)
        VALUES
            (-97101, -97101, 1, 1, true, 7, 4, 1, 0, NULL),
            (-97101, -97101, 1, 1, true, 7, 4, 1, 0, NULL),
            (-97101, -97102, 1, 1, false, 8, 8, 0, 0, false),
            (-97102, -97102, 1, 1, true, 8, 8, 0, 0, true),
            (-97101, -97104, 1, 1, true, 9, 9, 0, 0, true)
    """))
    for crash_id, muni, segment, year, severity in [
        (-97101, -97101, -97101, 2024, "fatal"),
        (-97102, -97101, -97101, 2024, "injury_unknown"),
        (-97103, -97101, -97102, 2024, "serious_injury"),
        (-97104, -97101, None, 2024, "property_damage"),
        (-97105, -97102, -97101, 2024, "fatal"),
        (-97106, -97101, -97101, 2022, "fatal"),
    ]:
        insert_crash(conn, crash_id, muni, "POINT(-74.5 40.5)",
                     segment_id=segment, crash_date=date(year, 6, 1),
                     severity=severity, bike=None)
    conn.execute(text("UPDATE crashes SET total_killed = 2, total_injured = 3 WHERE crash_id = -97101"))
    analysis = SimpleNamespace(analysis_id=-97101, muni_id=-97101,
                               start_year=2023, end_year=2024)
    with count_statements(conn) as statements:
        result = collect_report_evidence(db, analysis)
    assert len(statements) <= 5
    assert result["annual"][0]["total_killed"] is None
    assert result["annual"][1]["total_crashes"] == 4
    assert result["annual"][1]["total_killed"] is None
    assert result["annual"][1]["bike_unknown"] == 4
    network = result["network"]
    assert network["loaded_road_miles"] == pytest.approx(3.005)
    assert network["eligible_road_miles"] == 3
    assert network["selected_miles"] == 1
    assert network["selected_segments"] == 1
    assert network["assigned_crashes"] == 3
    assert network["selected_crashes"] == 2
    assert network["selected_fatal_crashes"] == 1
    assert network["selected_killed"] is None
    assert network["crash_share_pct"] == 50
    assert len(result["corridors"]) == 1
    corridor = result["corridors"][0]
    assert corridor["crashes"] == 4  # Saved HIN result, not refreshed count.
    assert corridor["rate"] == 2
    assert corridor["injury_unknown_crashes"] == 1
    assert corridor["segment_ids"] == [-97101]
    assert result["equity"]["unknown_miles"] == 1
    assert result["equity"]["known_segments"] == 0
    assert result["locations"] == [{"method": "unknown", "count": 4}]


def test_known_zero_casualties_and_ungrouped_corridors_remain_distinct(metric_db):
    from app.services.report_evidence import collect_report_evidence

    db, conn, _ = metric_db
    insert_municipality(conn, -97201)
    insert_analysis(conn, -97201, -97201, start_year=2023, end_year=2024)
    for segment in [-97201, -97202]:
        insert_road(conn, segment, -97201, "LINESTRING(-74.5 40.5,-74.4 40.5)",
                    road_name="Same road name", length_miles=1)
        insert_crash(conn, segment, -97201, "POINT(-74.5 40.5)", segment_id=segment,
                     crash_date=date(2023 if segment == -97201 else 2024, 6, 1))
    conn.execute(text("""
        INSERT INTO hin_segments
            (analysis_id, segment_id, severity_score, crash_rate, is_significant,
             crash_count_total, crash_count_fatal, crash_count_serious_injury,
             in_vulnerable_tract)
        VALUES (-97201, -97201, 1, 1, true, 1, 0, 0, false),
               (-97201, -97202, 1, 1, true, 1, 0, 0, true)
    """))
    conn.execute(text("""
        UPDATE crashes SET total_killed = 0,
            total_injured = CASE WHEN crash_id = -97201 THEN 0 ELSE 2 END
        WHERE muni_id = -97201
    """))
    result = collect_report_evidence(db, SimpleNamespace(
        analysis_id=-97201, muni_id=-97201, start_year=2023, end_year=2024,
    ))
    assert result["annual"][0]["total_killed"] == 0
    assert result["annual"][0]["total_injured"] == 0
    assert result["annual"][1]["total_injured"] == 2
    assert result["network"]["selected_killed"] == 0
    assert result["network"]["selected_injured"] == 2
    assert len(result["corridors"]) == 2
    assert [row["segment_ids"] for row in result["corridors"]] == [[-97202], [-97201]]
    assert all(row["corridor_id"] is None for row in result["corridors"])
    assert result["equity"]["known_segments"] == 2
    assert result["equity"]["unknown_segments"] == 0
    assert result["equity"]["known_miles"] == 2
    assert result["equity"]["high_vulnerability_miles"] == 1
