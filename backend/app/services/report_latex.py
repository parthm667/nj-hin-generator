"""Evidence-driven SS4A report text. External values are always literal TeX text."""

from datetime import datetime, timezone
from pathlib import Path
from string import Template

from app.services.methodology import METHOD_VERSION


class ReportTemplate(Template):
    delimiter = '@@'


def escape_latex(value):
    replacements = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
                    '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
                    '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(
        replacements.get(c, c) for c in ' '.join(str(value).replace('\x00', '').split()))


def number(value, places=0):
    if value is None:
        return 'Not available'
    if isinstance(value, (int, float)):
        return f'{value:,.{places}f}'
    return escape_latex(value)


def percent(value):
    return 'Not available' if value is None else number(value, 1) + r'\%'


def table(headers, rows, columns, caption):
    """Rows contain already-escaped text; headers and captions are application text."""
    header = ' & '.join(r'\textbf{' + escape_latex(h) + '}' for h in headers) + r' \\'
    body = '\n'.join(' & '.join(row) + r' \\' for row in rows)
    return (r'\begin{small}\setlength{\tabcolsep}{3pt}' + '\n'
            + r'\begin{longtable}{@{}' + columns + r'@{}}' + '\n'
            + r'\caption{' + escape_latex(caption) + r'}\\' + '\n'
            + r'\toprule ' + header + r' \midrule\endfirsthead' + '\n'
            + r'\multicolumn{' + str(len(headers)) + r'}{l}{\emph{Table \thetable\ continued}}\\' + '\n'
            + r'\toprule ' + header + r' \midrule\endhead' + '\n'
            + r'\bottomrule\endfoot' + '\n' + body + '\n' + r'\end{longtable}\end{small}')


def render_report_tex(analysis, municipality, stats, hin_stats, ranked_segments, evidence):
    evidence = evidence or {}
    network = evidence.get('network', {})
    name = escape_latex(municipality.name if municipality else 'Municipality')
    county = escape_latex(municipality.county if municipality else 'Not recorded')
    annual = evidence.get('annual', [])
    annual_rows = [[str(row['year'])] + [number(row.get(key)) for key in (
        'total_crashes', 'fatal_crashes', 'serious_injury_crashes', 'injury_unknown_crashes',
        'total_killed', 'total_injured')] for row in annual]
    annual_table = table(['Year', 'Loaded crashes', 'Fatal crashes', 'Serious-injury crashes',
                          'Injury detail unknown', 'People killed', 'People injured'], annual_rows,
                         'L{31pt}R{51pt}R{49pt}R{66pt}R{70pt}R{56pt}R{58pt}',
                         'Annual counts within the selected municipality and period.') if annual else 'Annual evidence is not available.'
    severity_rows = []
    for key, label in [('fatal', 'Fatal'), ('serious_injury', 'Serious injury'), ('minor_injury', 'Minor injury'),
                       ('injury_unknown', 'Injury; detail unknown'), ('property_damage', 'Property damage')]:
        count = stats.get(key)
        share = count / stats['total'] * 100 if stats.get('total') and count is not None else None
        severity_rows.append([label, number(count), percent(share)])
    severity_table = table(['Recorded severity', 'Crashes', 'Share of loaded crashes'], severity_rows,
                           'L{215pt}R{70pt}R{145pt}', 'Crash severity classification; counts are incidents, not people.')
    killed = stats.get('total_killed', 'Unknown')
    injured = stats.get('total_injured', 'Unknown')
    narrative = (f'For {name}, the loaded {analysis.start_year}--{analysis.end_year} records contain '
                 f"{number(stats.get('total'))} locatable crashes, including {number(stats.get('fatal'))} fatal crashes. ")
    narrative += (f'{number(killed)} people were recorded as killed. ' if isinstance(killed, (int, float))
                  else 'A complete person-level fatality total is not available for the loaded records. ')
    if network.get('selected_crashes') is not None:
        narrative += (f"The selected screening network contains {number(network.get('selected_miles'), 2)} miles "
                      f"and {number(network['selected_crashes'])} assigned crashes, representing "
                      f"{percent(network.get('crash_share_pct'))} of all loaded crashes in this period. ")
    narrative += ('These are recorded, locatable crashes in the loaded dataset; they are not a verified complete crash census. '
                  'The results support further investigation and project development, not a predicted crash reduction.')
    coverage_rows = [[label, number(network.get(key), places)] for label, key, places in [
        ('All loaded roadway mileage', 'loaded_road_miles', 2),
        ('Loaded roadway mileage eligible for screening', 'eligible_road_miles', 2),
        ('Selected network mileage', 'selected_miles', 2),
        ('Selected segments', 'selected_segments', 0),
        ('Loaded crashes with a road assignment', 'assigned_crashes', 0),
        ('Crashes assigned to selected segments', 'selected_crashes', 0),
        ('Fatal crashes assigned to selected segments', 'selected_fatal_crashes', 0),
        ('Recorded serious-injury crashes on selected segments', 'selected_serious_injury_crashes', 0),
        ('People killed on selected segments', 'selected_killed', 0),
        ('People injured on selected segments', 'selected_injured', 0)]]
    coverage_rows += [[label, percent(network.get(key))] for label, key in [
        ('Selected miles / eligible loaded roadway miles', 'road_share_pct'),
        ('Selected crashes / all loaded crashes', 'crash_share_pct'),
        ('Selected crashes / assigned crashes', 'assigned_share_pct')]]
    network_table = table(['Measure and denominator', 'Value'], coverage_rows,
                          'L{345pt}R{90pt}', 'Concentration within the loaded roadway and crash datasets.')
    corridor_rows = []
    for rank, row in enumerate(evidence.get('corridors', [])[:10], 1):
        corridor_rows.append([str(rank), escape_latex(row.get('name') or 'Unnamed corridor'),
                              number(row.get('segment_count')), number(row.get('miles'), 2),
                              number(row.get('crashes')), number(row.get('fatal_crashes')),
                              number(row.get('serious_injury_crashes')), number(row.get('injury_unknown_crashes')),
                              number(row.get('rate'), 2)])
    corridors = table(['No.', 'Stored corridor name', 'Seg.', 'Miles', 'Crashes', 'Fatal', 'Serious', 'Injury detail unknown', 'Rate'],
                      corridor_rows, 'L{20pt}L{127pt}R{25pt}R{35pt}R{42pt}R{30pt}R{38pt}R{60pt}R{36pt}',
                      'Up to ten selected corridors, ordered by stored assigned crash count.') if corridor_rows else (
                          'No corridor evidence is available. If no segments met the screening criteria, this does not establish that roads are safe.')
    user_rows = []
    if annual:
        for label, key in [('Pedestrian-involved crashes', 'ped_crashes'),
                           ('Bicycle involvement recorded yes', 'bike_crashes'),
                           ('Bicycle involvement unknown', 'bike_unknown')]:
            user_rows.append([label, number(sum(r[key] for r in annual))])
    else:
        user_rows.append(['Road-user involvement counts', 'Not available'])
    user_rows += [[label, number(stats.get(key))] for label, key in [
        ('All people killed', 'total_killed'), ('All people injured', 'total_injured'),
        ('Pedestrians killed', 'ped_killed'), ('Pedestrians injured', 'ped_injured'),
        ('Bicyclists killed', 'bike_killed'), ('Bicyclists injured', 'bike_injured')]]
    users = table(['Measure', 'Recorded count'], user_rows, 'L{340pt}R{95pt}', 'Road-user involvement and person counts.')
    locations = evidence.get('locations', [])
    location_rows = [[escape_latex(r['method'].replace('_', ' ')), number(r['count'])] for r in locations]
    location_table = table(['Stored location method', 'Loaded crashes'], location_rows,
                           'L{340pt}R{95pt}', 'Location methods recorded for the selected period.') if locations else 'Location-method counts are not available.'
    equity = evidence.get('equity', {})
    equity_rows = [[label, number(equity.get(key), places)] for label, key, places in [
        ('Selected miles with known vulnerability flag', 'known_miles', 2),
        ('Selected miles flagged above the 75th percentile', 'high_vulnerability_miles', 2),
        ('Selected miles with unknown vulnerability flag', 'unknown_miles', 2),
        ('Selected segments with known flag', 'known_segments', 0),
        ('Selected segments with unknown flag', 'unknown_segments', 0)]]
    equity_table = table(['Stored geographic context', 'Value'], equity_rows,
                         'L{340pt}R{95pt}', 'Social-vulnerability context of selected segments, where loaded.')
    segment_rows = [[str(rank), escape_latex(road.road_name or 'Unnamed road'),
                     escape_latex(road.segment_id), number(hin.crash_count_total),
                     number(hin.crash_rate, 2), number(road.length_miles, 3)]
                    for rank, (hin, road) in enumerate(ranked_segments, 1)]
    segments = table(['Map no.', 'Road name', 'Segment ID', 'Crashes', 'Rate', 'Miles'], segment_rows,
                     'L{32pt}L{179pt}R{58pt}R{46pt}R{48pt}R{45pt}',
                     'Top ten selected segments by recorded crashes per mile per year.') if segment_rows else 'No ranked segments are available.'
    versions = getattr(analysis, 'input_version', None) or {}
    version_rows = [[escape_latex(k), escape_latex(versions.get(k, 'Not recorded'))] for k in ('crashes', 'roads', 'boundaries', 'svi')]
    version_table = table(['Dataset', 'Stored revision identifier'], version_rows,
                          'L{95pt}L{340pt}', 'Reproducibility identifiers; these are not collection dates.')
    created = getattr(analysis, 'created_at', None)
    if created and created.tzinfo:
        created = created.astimezone(timezone.utc)
    values = dict(NAME=name, COUNTY=county, START=str(analysis.start_year), END=str(analysis.end_year),
                  ANALYSIS_ID=escape_latex(getattr(analysis, 'analysis_id', 'Not recorded')),
                  RUN_DATE=escape_latex(created.strftime('%Y-%m-%d %H:%M UTC') if created else 'Not recorded'),
                  GENERATED=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                  TOTAL=number(stats.get('total')), KILLED=number(killed), INJURED=number(injured),
                  UNKNOWN_SEVERITY=number(stats.get('injury_unknown')), NARRATIVE=narrative,
                  ANNUAL=annual_table, SEVERITY=severity_table, NETWORK=network_table,
                  CORRIDORS=corridors, USERS=users, LOCATIONS=location_table, EQUITY=equity_table,
                  SEGMENTS=segments, VERSIONS=version_table, METHOD=escape_latex(METHOD_VERSION),
                  SNAP=number(analysis.snap_distance_meters), THRESHOLD=number(analysis.significance_threshold, 3))
    template = Path(__file__).resolve().parents[1] / 'templates' / 'ss4a_report.tex'
    return ReportTemplate(template.read_text(encoding='utf-8')).substitute(values)
