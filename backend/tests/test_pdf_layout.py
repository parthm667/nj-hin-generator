"""Report export guards and real LaTeX integration, without a database."""
from types import SimpleNamespace as Record
from unittest.mock import MagicMock, Mock
import shutil

import pytest

from app.services import pdf_service
from app.services.pdf_map import NetworkMap
from app.services.pdf_service import PDFReportGenerator
from test_report_latex import fixture


def test_export_rejects_stale_results_before_collecting_report_data(monkeypatch):
    analysis = Record(status='completed')
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = analysis
    guard = Mock()
    guard.require_current_results.side_effect = ValueError('stale results')
    monkeypatch.setattr(pdf_service, 'CoverageService', Mock(return_value=guard))
    report = PDFReportGenerator(db)
    report._get_detailed_crash_statistics = Mock()
    with pytest.raises(ValueError, match='stale results'):
        report.generate_report(42)
    report._get_detailed_crash_statistics.assert_not_called()


def test_export_collects_evidence_and_compiles_latex_with_vector_map(monkeypatch):
    analysis, municipality, stats, hin, ranked, evidence = fixture()
    analysis.status = 'completed'
    analysis.muni_id = 1
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [analysis, municipality]
    monkeypatch.setattr(pdf_service, 'CoverageService', Mock(return_value=Mock()))
    evidence_loader = Mock(return_value=evidence)
    monkeypatch.setattr(pdf_service, 'collect_report_evidence', evidence_loader)
    monkeypatch.setattr(pdf_service, 'build_network_map', Mock(return_value=NetworkMap()))
    compiled = Mock(return_value=b'%PDF-test')
    monkeypatch.setattr(pdf_service, 'compile_latex', compiled)
    report = PDFReportGenerator(db)
    report._get_detailed_crash_statistics = Mock(return_value=stats)
    report._get_hin_statistics = Mock(return_value=hin)
    report._get_ranked_segments = Mock(return_value=ranked)
    assert report.generate_report(42) == b'%PDF-test'
    evidence_loader.assert_called_once_with(db, analysis)
    source, assets = compiled.call_args.args
    assert 'SS4A' in source
    assert '25.0\\%' in source
    assert assets['network.pdf'].startswith(b'%PDF-')
    assert set(assets) == {'network.pdf'}


@pytest.mark.skipif(shutil.which('pdflatex') is None, reason='pdflatex is not installed')
def test_real_latex_report_compiles_with_escaped_municipality():
    analysis, municipality, stats, hin, ranked, evidence = fixture()
    source, assets = PDFReportGenerator.report_sources(
        analysis, municipality, stats, hin, ranked, NetworkMap(), evidence)
    result = pdf_service.compile_latex(source, assets)
    assert result.startswith(b'%PDF-')
    assert len(result) > 10000
