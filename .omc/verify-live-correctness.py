"""Read-only local API/PostGIS comparison for the new Avalon result."""
import csv
import io
import json
from urllib.request import urlopen

import psycopg2

BASE = 'http://127.0.0.1:58001/api'


def get(path):
    with urlopen(BASE + path, timeout=30) as response:
        return json.load(response)


analysis = get('/analysis/7')
assert analysis['status'] == 'completed' and analysis['data_status'] == 'ready'
assert analysis['total_crashes'] == 133 and analysis['total_injuries'] == 41
with psycopg2.connect('postgresql://test_user:test_password@127.0.0.1:55432/nj_hin_real') as db:
    with db.cursor() as cursor:
        cursor.execute("SELECT crash_id, ST_AsGeoJSON(geom)::json FROM crashes WHERE muni_id=9 AND crash_date BETWEEN '2017-01-01' AND '2021-12-31'")
        expected_crashes = dict(cursor.fetchall())
        cursor.execute('SELECT h.hin_id, ST_AsGeoJSON(r.geom)::json FROM hin_segments h JOIN road_segments r USING(segment_id) WHERE h.analysis_id=7 AND h.is_significant')
        expected_hin = dict(cursor.fetchall())
crashes = get('/analysis/7/crashes')['features']
hin = get('/analysis/7/hin')['features']
assert {f['properties']['crash_id']: f['geometry'] for f in crashes} == expected_crashes
assert {f['properties']['hin_id']: f['geometry'] for f in hin} == expected_hin
assert len(crashes) == 133 and len(hin) == 9
assert all(f['properties']['bike_involved'] is None for f in crashes)
assert sum(f['properties']['total_injured'] for f in crashes) == 41
assert all(len(f['geometry']['coordinates']) >= 2 for f in hin)
for layer, expected in [('crashes', 133), ('hin_segments', 9)]:
    with urlopen(BASE + '/export/7/csv?data_type=' + layer, timeout=30) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode())))
    assert len(rows) == expected
    if layer == 'crashes':
        assert all(row['bike_involved'] == '' for row in rows)
print('PASS: exact PostGIS/API geometries for 133 crashes and 9 HIN lines; casualty sums, unknown bicycle fields and both CSV exports match.')
