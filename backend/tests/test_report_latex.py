from types import SimpleNamespace as Record

from app.services.report_latex import escape_latex, render_report_tex


def fixture():
    analysis = Record(analysis_id=42, start_year=2017, end_year=2021,
                      snap_distance_meters=50, significance_threshold=.05,
                      created_at=None, input_version={})
    municipality = Record(name=r'Test & Town \input{secret}', county='Mercer')
    stats = dict(total=100, fatal=1, serious_injury=0, minor_injury=0,
                 injury_unknown=20, property_damage=79, total_killed=2,
                 total_injured='Unknown', ped_killed=0, ped_injured=3,
                 bike_killed='Unknown', bike_injured='Unknown')
    evidence = dict(annual=[], corridors=[], locations=[], equity={}, network=dict(
        loaded_crashes=100, selected_crashes=25, selected_miles=2,
        eligible_road_miles=20, road_share_pct=10, crash_share_pct=25,
        assigned_crashes=80, assigned_share_pct=31.25))
    return analysis, municipality, stats, dict(segment_count=3, total_miles=2), [], evidence


def test_escapes_tex_commands_and_every_special_character():
    escaped = escape_latex(r'\input{/etc/passwd} & 20% # $_~^')
    assert r'\input{' not in escaped
    assert r'\textbackslash{}input\{' in escaped
    for item in [r'\&', r'\%', r'\#', r'\$', r'\_', r'\textasciitilde{}', r'\textasciicircum{}']:
        assert item in escaped


def test_report_contains_funding_evidence_and_explicit_denominators():
    source = render_report_tex(*fixture())
    for section in ['Annual crash history', 'Network concentration', 'Corridor evidence',
                    'Project development worksheet', 'Action Plan components', 'SS4A']:
        assert section in source
    assert '25.0\\%' in source
    assert '31.2\\%' in source
    assert 'all loaded crashes' in source
    assert 'assigned crashes' in source
    assert 'Unknown' in source
    assert r'\input{secret}' not in source


def test_missing_evidence_is_not_filled_with_zero_or_a_funding_claim():
    values = list(fixture())
    values[-1] = {}
    source = render_report_tex(*values)
    assert 'Not available' in source
    assert 'does not establish funding eligibility' in source
    assert 'seriously injured people' in source
    assert '2019--2023' in source
