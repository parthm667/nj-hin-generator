"""
PDF Report Generation Service

Generates professional PDF reports for High Injury Network analyses
suitable for grant applications (SS4A, HSIP, etc.)
"""

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, KeepTogether
)
from reportlab.pdfgen import canvas

from sqlalchemy.orm import Session
from backend.app.models.tables import Analysis, Municipality, Crash, HINSegment

logger = logging.getLogger(__name__)


class PDFReportGenerator:
    """Generates PDF reports for HIN analyses."""

    def __init__(self, db: Session):
        """Initialize the PDF generator."""
        self.db = db
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Set up custom paragraph styles."""
        # Title style
        self.styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=30,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))

        # Subtitle style
        self.styles.add(ParagraphStyle(
            name='CustomSubtitle',
            parent=self.styles['Normal'],
            fontSize=14,
            textColor=colors.HexColor('#64748b'),
            spaceAfter=30,
            alignment=TA_CENTER,
            fontName='Helvetica'
        ))

        # Section heading
        self.styles.add(ParagraphStyle(
            name='SectionHeading',
            parent=self.styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=12,
            spaceBefore=20,
            fontName='Helvetica-Bold'
        ))

        # Body with justify
        self.styles.add(ParagraphStyle(
            name='BodyJustify',
            parent=self.styles['Normal'],
            fontSize=11,
            alignment=TA_JUSTIFY,
            spaceAfter=12,
            leading=14
        ))

    def generate_report(self, analysis_id: int) -> bytes:
        """
        Generate PDF report for an analysis.

        Args:
            analysis_id: Analysis ID

        Returns:
            PDF bytes
        """
        # Fetch analysis
        analysis = self.db.query(Analysis).filter(
            Analysis.analysis_id == analysis_id
        ).first()

        if not analysis:
            raise ValueError(f"Analysis {analysis_id} not found")

        if analysis.status != 'completed':
            raise ValueError(f"Analysis {analysis_id} is not completed (status: {analysis.status})")

        # Fetch municipality
        municipality = self.db.query(Municipality).filter(
            Municipality.muni_id == analysis.muni_id
        ).first()

        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
        )

        # Build content
        story = []

        # Cover page
        story.extend(self._build_cover_page(analysis, municipality))
        story.append(PageBreak())

        # Executive summary
        story.extend(self._build_executive_summary(analysis, municipality))
        story.append(PageBreak())

        # Crash statistics
        story.extend(self._build_crash_statistics(analysis, municipality))
        story.append(PageBreak())

        # High Injury Network results
        story.extend(self._build_hin_results(analysis, municipality))
        story.append(PageBreak())

        # Methodology
        story.extend(self._build_methodology(analysis))

        # Build PDF
        doc.build(story, onFirstPage=self._add_footer, onLaterPages=self._add_footer)

        # Get PDF bytes
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes

    def _build_cover_page(self, analysis: Analysis, municipality: Municipality) -> List:
        """Build cover page."""
        elements = []

        # Spacer
        elements.append(Spacer(1, 2*inch))

        # Title
        title = Paragraph(
            "High Injury Network Analysis",
            self.styles['CustomTitle']
        )
        elements.append(title)

        # Municipality
        if municipality:
            subtitle = Paragraph(
                f"{municipality.name}, {municipality.county} County, New Jersey",
                self.styles['CustomSubtitle']
            )
            elements.append(subtitle)

        # Date range
        date_range = Paragraph(
            f"Analysis Period: {analysis.start_year} - {analysis.end_year}",
            self.styles['CustomSubtitle']
        )
        elements.append(date_range)

        # Spacer
        elements.append(Spacer(1, 1*inch))

        # Report date
        report_date = Paragraph(
            f"Report Generated: {datetime.now().strftime('%B %d, %Y')}",
            self.styles['Normal']
        )
        elements.append(report_date)

        return elements

    def _build_executive_summary(self, analysis: Analysis, municipality: Municipality) -> List:
        """Build executive summary section."""
        elements = []

        # Section title
        elements.append(Paragraph("Executive Summary", self.styles['SectionHeading']))

        # Get crash statistics
        crash_stats = self._get_crash_statistics(analysis)
        hin_stats = self._get_hin_statistics(analysis)

        # Summary text
        summary_text = f"""
        This report presents the results of a High Injury Network (HIN) analysis for
        {municipality.name if municipality else 'the municipality'}, conducted for the period
        {analysis.start_year} through {analysis.end_year}.
        The analysis identified roadway segments with elevated crash rates and severity,
        focusing resources on locations with the greatest potential for safety improvements.
        """

        elements.append(Paragraph(summary_text, self.styles['BodyJustify']))
        elements.append(Spacer(1, 0.2*inch))

        # Key findings
        elements.append(Paragraph("Key Findings", self.styles['Heading3']))

        findings = [
            f"Total crashes analyzed: {crash_stats['total']:,}",
            f"Fatal crashes: {crash_stats['fatal']}",
            f"Serious injury crashes: {crash_stats['serious_injury']}",
            f"Total fatalities: {crash_stats['total_killed']}",
            f"Total injuries: {crash_stats['total_injured']}",
            f"High Injury Network length: {hin_stats['total_miles']:.1f} miles",
            f"HIN segments identified: {hin_stats['segment_count']}",
        ]

        for finding in findings:
            elements.append(Paragraph(f"• {finding}", self.styles['Normal']))

        return elements

    def _build_crash_statistics(self, analysis: Analysis, municipality: Municipality) -> List:
        """Build crash statistics section."""
        elements = []

        elements.append(Paragraph("Crash Statistics", self.styles['SectionHeading']))

        # Get detailed crash stats
        crash_stats = self._get_detailed_crash_statistics(analysis)

        # Create table
        table_data = [
            ['Severity', 'Count', 'Percentage'],
            ['Fatal', f"{crash_stats['fatal']}", f"{crash_stats['fatal_pct']:.1f}%"],
            ['Serious Injury', f"{crash_stats['serious_injury']}", f"{crash_stats['serious_injury_pct']:.1f}%"],
            ['Minor Injury', f"{crash_stats['minor_injury']}", f"{crash_stats['minor_injury_pct']:.1f}%"],
            ['Property Damage', f"{crash_stats['property_damage']}", f"{crash_stats['property_damage_pct']:.1f}%"],
            ['Total', f"{crash_stats['total']}", "100.0%"],
        ]

        table = Table(table_data, colWidths=[2.5*inch, 1.5*inch, 1.5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e0e7ff')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 0.3*inch))

        # Vulnerable users
        elements.append(Paragraph("Vulnerable Road Users", self.styles['Heading3']))

        vuln_data = [
            ['Category', 'Killed', 'Injured'],
            ['Pedestrians', f"{crash_stats['ped_killed']}", f"{crash_stats['ped_injured']}"],
            ['Bicyclists', f"{crash_stats['bike_killed']}", f"{crash_stats['bike_injured']}"],
        ]

        vuln_table = Table(vuln_data, colWidths=[2.5*inch, 1.5*inch, 1.5*inch])
        vuln_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ]))

        elements.append(vuln_table)

        return elements

    def _build_hin_results(self, analysis: Analysis, municipality: Municipality) -> List:
        """Build HIN results section."""
        elements = []

        elements.append(Paragraph("High Injury Network Results", self.styles['SectionHeading']))

        # Description
        desc_text = """
        The High Injury Network represents roadway segments that experience disproportionately
        high numbers of fatal and serious injury crashes. These segments were identified using
        statistical methods to determine which locations have crash rates significantly above
        the expected baseline.
        """
        elements.append(Paragraph(desc_text, self.styles['BodyJustify']))
        elements.append(Spacer(1, 0.2*inch))

        # Get significant HIN segments joined to their road geometry/metadata
        from backend.app.models.tables import RoadSegment
        hin_segments = self.db.query(HINSegment, RoadSegment).join(
            RoadSegment, HINSegment.segment_id == RoadSegment.segment_id
        ).filter(
            HINSegment.analysis_id == analysis.analysis_id,
            HINSegment.is_significant == True
        ).order_by(HINSegment.crash_rate.desc()).limit(10).all()

        if hin_segments:
            elements.append(Paragraph("Top 10 High Injury Network Segments", self.styles['Heading3']))

            # Create table
            table_data = [['Rank', 'Road Name', 'Crashes', 'Rate', 'Length (mi)']]

            for idx, (hin, road) in enumerate(hin_segments, 1):
                table_data.append([
                    str(idx),
                    (road.road_name or 'Unnamed')[:30],
                    str(hin.crash_count_total),
                    f"{hin.crash_rate:.2f}",
                    f"{road.length_miles:.2f}"
                ])

            hin_table = Table(table_data, colWidths=[0.6*inch, 2.5*inch, 1*inch, 1*inch, 1*inch])
            hin_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ]))

            elements.append(hin_table)
        else:
            elements.append(Paragraph("No HIN segments identified.", self.styles['Normal']))

        return elements

    def _build_methodology(self, analysis: Analysis) -> List:
        """Build methodology section."""
        elements = []

        elements.append(Paragraph("Methodology", self.styles['SectionHeading']))

        method_text = """
        This analysis follows best practices for High Injury Network identification as
        outlined in FHWA guidance and used by Vision Zero cities nationwide.
        """
        elements.append(Paragraph(method_text, self.styles['BodyJustify']))
        elements.append(Spacer(1, 0.2*inch))

        elements.append(Paragraph("Analysis Steps", self.styles['Heading3']))

        steps = [
            "Crash Data Collection: Official crash records from NJ Department of Transportation",
            "Spatial Processing: Crashes snapped to nearest road segment within 50 meters",
            "Severity Weighting: Fatal crashes weighted 4x, serious injury 3x, minor injury 2x",
            "Rate Calculation: Crash rates calculated per segment mile per year",
            "Statistical Testing: Poisson distribution used to identify statistically significant crash rates",
            f"Significance Threshold: P-value < {analysis.significance_threshold}",
            "Network Assembly: Contiguous high-crash segments grouped into corridors"
        ]

        for step in steps:
            elements.append(Paragraph(f"• {step}", self.styles['Normal']))

        elements.append(Spacer(1, 0.2*inch))
        elements.append(Paragraph("Data Sources", self.styles['Heading3']))

        sources = [
            "Crash Data: NJ Department of Transportation (NJDOT) via NJ Open Data Portal",
            "Road Network: OpenStreetMap",
            "Municipal Boundaries: NJ Office of GIS",
        ]

        for source in sources:
            elements.append(Paragraph(f"• {source}", self.styles['Normal']))

        return elements

    def _add_footer(self, canvas, doc):
        """Add footer to page."""
        canvas.saveState()
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#64748b'))

        # Page number
        page_num = canvas.getPageNumber()
        text = f"Page {page_num}"
        canvas.drawRightString(7.5*inch, 0.5*inch, text)

        # Generated by
        canvas.drawString(0.75*inch, 0.5*inch, "NJ High Injury Network Generator")

        canvas.restoreState()

    def _get_crash_statistics(self, analysis: Analysis) -> Dict:
        """Get basic crash statistics for the analysis municipality and period."""
        from sqlalchemy import func, case

        stats = self.db.query(
            func.count(Crash.crash_id).label('total'),
            func.sum(case((Crash.severity == 'fatal', 1), else_=0)).label('fatal'),
            func.sum(case((Crash.severity == 'serious_injury', 1), else_=0)).label('serious_injury'),
        ).filter(
            Crash.muni_id == analysis.muni_id,
            Crash.crash_date >= f"{analysis.start_year}-01-01",
            Crash.crash_date <= f"{analysis.end_year}-12-31"
        ).first()

        return {
            'total': stats.total or 0,
            'fatal': stats.fatal or 0,
            'serious_injury': stats.serious_injury or 0,
            # Per-crash casualty counts are not stored; use analysis summary.
            'total_killed': analysis.total_fatalities or 0,
            'total_injured': analysis.total_injuries or 0,
        }

    def _get_detailed_crash_statistics(self, analysis: Analysis) -> Dict:
        """Get detailed crash statistics with percentages."""
        from sqlalchemy import func, case

        stats = self.db.query(
            func.count(Crash.crash_id).label('total'),
            func.sum(case((Crash.severity == 'fatal', 1), else_=0)).label('fatal'),
            func.sum(case((Crash.severity == 'serious_injury', 1), else_=0)).label('serious_injury'),
            func.sum(case((Crash.severity == 'minor_injury', 1), else_=0)).label('minor_injury'),
            func.sum(case((Crash.severity == 'property_damage', 1), else_=0)).label('property_damage'),
            func.sum(case((Crash.ped_involved == True, 1), else_=0)).label('ped_killed'),
            func.sum(case((Crash.ped_involved == True, 1), else_=0)).label('ped_injured'),
            func.sum(case((Crash.bike_involved == True, 1), else_=0)).label('bike_killed'),
            func.sum(case((Crash.bike_involved == True, 1), else_=0)).label('bike_injured'),
        ).filter(
            Crash.muni_id == analysis.muni_id,
            Crash.crash_date >= f"{analysis.start_year}-01-01",
            Crash.crash_date <= f"{analysis.end_year}-12-31"
        ).first()

        total = stats.total or 1  # Avoid division by zero

        return {
            'total': stats.total or 0,
            'fatal': stats.fatal or 0,
            'fatal_pct': (stats.fatal or 0) / total * 100,
            'serious_injury': stats.serious_injury or 0,
            'serious_injury_pct': (stats.serious_injury or 0) / total * 100,
            'minor_injury': stats.minor_injury or 0,
            'minor_injury_pct': (stats.minor_injury or 0) / total * 100,
            'property_damage': stats.property_damage or 0,
            'property_damage_pct': (stats.property_damage or 0) / total * 100,
            'ped_killed': stats.ped_killed or 0,
            'ped_injured': stats.ped_injured or 0,
            'bike_killed': stats.bike_killed or 0,
            'bike_injured': stats.bike_injured or 0,
        }

    def _get_hin_statistics(self, analysis: Analysis) -> Dict:
        """Get HIN statistics from the stored analysis summary."""
        return {
            'segment_count': analysis.hin_segment_count or 0,
            'total_miles': analysis.hin_miles or 0,
        }
