"""LaTeX reports with stored analysis evidence and an offline vector map."""

import io
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.tables import Analysis, Municipality, HINSegment, RoadSegment
from app.services.crash_service import CrashService
from app.services.coverage_service import CoverageService
from app.services.pdf_map import build_network_map
from app.services.report_evidence import collect_report_evidence
from app.services.report_latex import render_report_tex
from app.services.latex_compiler import compile_latex


class PDFReportGenerator:
    def __init__(self, db: Session):
        self.db = db

    def generate_report(self, analysis_id: int) -> bytes:
        source, assets = self._collect_report_sources(analysis_id)
        return compile_latex(source, assets)

    def _collect_report_sources(self, analysis_id: int):
        analysis = self.db.query(Analysis).filter(Analysis.analysis_id == analysis_id).first()
        if not analysis:
            raise ValueError(f'Analysis {analysis_id} not found')
        if analysis.status != 'completed':
            raise ValueError(f'Analysis {analysis_id} is not completed (status: {analysis.status})')
        CoverageService(self.db).require_current_results(analysis)
        municipality = self.db.query(Municipality).filter(Municipality.muni_id == analysis.muni_id).first()
        stats = self._get_detailed_crash_statistics(analysis)
        hin_stats = self._get_hin_statistics(analysis)
        ranked = self._get_ranked_segments(analysis)
        evidence = collect_report_evidence(self.db, analysis)
        network_map = build_network_map(self.db, analysis, municipality, ranked, 504, 300)
        return self.report_sources(analysis, municipality, stats, hin_stats, ranked, network_map, evidence)

    @staticmethod
    def report_sources(analysis, municipality, stats, hin_stats, ranked, network_map, evidence):
        """Return editable TeX and its fixed-name vector asset for the same report."""
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=(network_map.width, network_map.height))
        network_map.drawOn(canvas, 0, 0)
        canvas.save()
        source = render_report_tex(analysis, municipality, stats, hin_stats, ranked, evidence)
        return source, {'network.pdf': buffer.getvalue()}

    def _get_ranked_segments(self, analysis):
        # Match the evidence collector: the latest significant result for each
        # municipal segment wins before ranking and limiting unique priorities.
        selected = self.db.query(
            HINSegment.hin_id,
            func.row_number().over(
                partition_by=HINSegment.segment_id,
                order_by=HINSegment.hin_id.desc(),
            ).label('segment_rank'),
        ).join(RoadSegment, HINSegment.segment_id == RoadSegment.segment_id).filter(
            HINSegment.analysis_id == analysis.analysis_id,
            HINSegment.is_significant.is_(True),
            RoadSegment.muni_id == analysis.muni_id,
        ).subquery()
        return self.db.query(HINSegment, RoadSegment).join(
            RoadSegment, HINSegment.segment_id == RoadSegment.segment_id
        ).join(selected, selected.c.hin_id == HINSegment.hin_id).filter(
            selected.c.segment_rank == 1,
        ).order_by(
            HINSegment.crash_rate.desc(), HINSegment.segment_id.asc()
        ).limit(10).all()

    def _get_detailed_crash_statistics(self, analysis):
        stats = CrashService(self.db).get_municipality_crash_summary(analysis.muni_id, analysis.start_year, analysis.end_year)
        total = stats['total_crashes']
        result = {'total': total}
        for name in ('fatal', 'serious_injury', 'minor_injury', 'injury_unknown', 'property_damage'):
            count = stats[f'{name}_crashes']
            result[name] = count
            result[f'{name}_pct'] = count / total * 100 if total else 0
        for target, source in [('total_killed', 'total_killed'), ('total_injured', 'total_injured'),
                               ('ped_killed', 'pedestrians_killed'), ('ped_injured', 'pedestrians_injured')]:
            result[target] = stats[source] if stats[source] is not None else 'Unknown'
        result['bike_killed'] = result['bike_injured'] = 'Unknown'
        return result

    def _get_hin_statistics(self, analysis):
        return {'segment_count': analysis.hin_segment_count or 0, 'total_miles': analysis.hin_miles or 0}
