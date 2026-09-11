from sqlalchemy import text

from test_coverage import coverage_api, insert_municipality


def test_municipality_detail_reports_loaded_statistics_without_inventing_casualties(
    coverage_api,
):
    client, _, connection = coverage_api
    insert_municipality(connection, -997001)
    connection.execute(text("""
        INSERT INTO crashes
            (crash_id, external_id, muni_id, crash_date, severity,
             total_killed, total_injured, geom)
        VALUES
            (-997011, 'municipality-detail-1', -997001, '2019-01-02',
             'fatal', 2, 3, ST_SetSRID(ST_Point(-74.5, 40.5), 4326)),
            (-997012, 'municipality-detail-2', -997001, '2020-02-03',
             'injury_unknown', 0, NULL,
             ST_SetSRID(ST_Point(-74.5, 40.5), 4326))
    """))
    connection.execute(text("""
        INSERT INTO road_segments
            (segment_id, source_id, muni_id, road_name, road_class,
             length_miles, geom)
        VALUES
            (-997021, 'municipality-detail-road-1', -997001, 'A', 'local',
             1.25, ST_GeomFromText('LINESTRING(-74.6 40.5,-74.5 40.5)', 4326)),
            (-997022, 'municipality-detail-road-2', -997001, 'B', 'local',
             2.5, ST_GeomFromText('LINESTRING(-74.5 40.5,-74.4 40.5)', 4326))
    """))
    connection.execute(text("""
        INSERT INTO analyses
            (analysis_id, muni_id, created_at, start_year, end_year, status)
        VALUES
            (-997031, -997001, '2024-01-01 12:00:00', 2019, 2020, 'completed'),
            (-997032, -997001, '2024-02-03 04:05:06', 2019, 2020, 'failed')
    """))

    response = client.get('/api/municipalities/-997001')

    assert response.status_code == 200
    assert response.json() == {
        'muni_id': -997001,
        'name': 'Coverage 997001',
        'county': 'Mercer',
        'muni_code': 'coverage-997001',
        'total_crashes': 2,
        'total_fatalities': 2,
        'total_injuries': None,
        'road_miles': 3.75,
        'latest_analysis': '2024-02-03T04:05:06',
    }


def test_municipality_summary_defaults_to_actual_loaded_year_bounds_and_discloses_gaps(
    coverage_api,
):
    client, _, connection = coverage_api
    insert_municipality(connection, -997101)
    connection.execute(text("""
        INSERT INTO crashes
            (crash_id, external_id, muni_id, crash_date, severity,
             total_killed, total_injured, pedestrians_killed,
             pedestrians_injured, geom)
        VALUES
            (-997111, 'municipality-summary-1', -997101, '2018-01-02',
             'fatal', 1, 0, 0, 0,
             ST_SetSRID(ST_Point(-74.5, 40.5), 4326)),
            (-997112, 'municipality-summary-2', -997101, '2020-02-03',
             'property_damage', 0, 2, 0, 1,
             ST_SetSRID(ST_Point(-74.5, 40.5), 4326))
    """))

    response = client.get('/api/municipalities/-997101/summary')

    assert response.status_code == 200
    payload = response.json()
    assert payload['start_year'] == 2018
    assert payload['end_year'] == 2020
    assert payload['available_years'] == [2018, 2020]
    assert payload['missing_years'] == [2019]
    assert payload['total_crashes'] == 2
    assert payload['total_killed'] == 1
    assert payload['total_injured'] == 2
    assert payload['coverage_note'] == (
        'Counts include loaded, usable records only; available years and gaps do not '
        'certify source completeness.'
    )


def test_municipality_summary_honors_an_explicit_period_and_preserves_unknown_counts(
    coverage_api,
):
    client, _, connection = coverage_api
    insert_municipality(connection, -997201)
    connection.execute(text("""
        INSERT INTO crashes
            (crash_id, external_id, muni_id, crash_date, severity,
             total_killed, total_injured, pedestrians_killed,
             pedestrians_injured, geom)
        VALUES
            (-997211, 'municipality-explicit-1', -997201, '2018-01-02',
             'fatal', 1, 0, 0, 0,
             ST_SetSRID(ST_Point(-74.5, 40.5), 4326)),
            (-997212, 'municipality-explicit-2', -997201, '2020-02-03',
             'injury_unknown', NULL, NULL, NULL, NULL,
             ST_SetSRID(ST_Point(-74.5, 40.5), 4326))
    """))

    response = client.get(
        '/api/municipalities/-997201/summary?start_year=2020&end_year=2021'
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['start_year'] == 2020
    assert payload['end_year'] == 2021
    assert payload['available_years'] == [2018, 2020]
    assert payload['missing_years'] == [2021]
    assert payload['total_crashes'] == 1
    assert payload['total_killed'] is None
    assert payload['total_injured'] is None
    assert payload['casualty_counts_complete'] is False


def test_municipality_summary_without_loaded_data_does_not_label_casualties_zero(
    coverage_api,
):
    client, _, connection = coverage_api
    insert_municipality(connection, -997301)

    response = client.get('/api/municipalities/-997301/summary')

    assert response.status_code == 200
    payload = response.json()
    assert payload['start_year'] is None
    assert payload['end_year'] is None
    assert payload['available_years'] == []
    assert payload['missing_years'] == []
    assert payload['total_crashes'] == 0
    assert payload['total_killed'] is None
    assert payload['total_injured'] is None
    assert payload['pedestrians_killed'] is None
    assert payload['pedestrians_injured'] is None
    assert payload['casualty_counts_complete'] is False


def test_municipality_summary_requires_a_complete_valid_explicit_period(coverage_api):
    client, _, connection = coverage_api
    insert_municipality(connection, -997401)

    missing_end = client.get(
        '/api/municipalities/-997401/summary?start_year=2020'
    )
    reversed_range = client.get(
        '/api/municipalities/-997401/summary?start_year=2021&end_year=2020'
    )

    assert missing_end.status_code == 422
    assert missing_end.json()['detail'] == (
        'Provide both start_year and end_year, or omit both to use loaded years.'
    )
    assert reversed_range.status_code == 422
    assert reversed_range.json()['detail'] == (
        'start_year must be less than or equal to end_year'
    )
