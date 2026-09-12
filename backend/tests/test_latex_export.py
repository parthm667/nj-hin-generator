import io
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.database import get_db
from app.services import api_safeguards, pdf_service
from app.services.coverage_service import AnalysisDataAssessment, AnalysisDataConflictError, CoverageService
from app.services.pdf_map import NetworkMap
from app.services.pdf_service import PDFReportGenerator
from app.services.request_limits import RequestLimitExceeded
from test_report_latex import fixture


def test_source_archive_contains_editable_report_and_map_without_compiling(monkeypatch):
    analysis, municipality, stats, hin, ranked, evidence = fixture()
    analysis.status, analysis.muni_id = 'completed', 1
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [analysis, municipality]
    monkeypatch.setattr(CoverageService, 'require_current_results', lambda *args: None)
    monkeypatch.setattr(pdf_service, 'collect_report_evidence', lambda *args: evidence)
    monkeypatch.setattr(pdf_service, 'build_network_map', lambda *args: NetworkMap())

    def unexpected_compile(*args):
        raise AssertionError('Source download must not invoke a TeX compiler')

    monkeypatch.setattr(pdf_service, 'compile_latex', unexpected_compile)
    report = PDFReportGenerator(db)
    report._get_detailed_crash_statistics = Mock(return_value=stats)
    report._get_hin_statistics = Mock(return_value=hin)
    report._get_ranked_segments = Mock(return_value=ranked)
    data = report.generate_latex_archive(42)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {'report.tex', 'network.pdf', 'README.txt'}
        source = archive.read('report.tex').decode('utf-8')
        assert r'\documentclass' in source
        assert r'\input{secret}' not in source
        assert 'SS4A' in source
        assert r'25.0\%' in source
        assert archive.read('network.pdf').startswith(b'%PDF-')
        instructions = archive.read('README.txt').decode('utf-8')
        assert instructions.count('pdflatex -no-shell-escape') == 2


@pytest.fixture
def export_client(monkeypatch):
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(api_safeguards, 'consume_request_limit', lambda *args: None)
    monkeypatch.setattr(api_safeguards.settings, 'anonymous_rate_limit_secret', 'test-secret')
    with TestClient(app) as client:
        yield client, db
    app.dependency_overrides.clear()


@pytest.mark.parametrize('status, expected', [(None, 404), ('running', 400)])
def test_source_endpoint_rejects_absent_or_unfinished_analysis(export_client, status, expected):
    client, db = export_client
    db.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(status=status) if status else None)
    assert client.get('/api/analysis/42/export/latex').status_code == expected


def test_source_endpoint_rejects_stale_results(export_client, monkeypatch):
    client, db = export_client
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(status='completed')

    def reject(*args):
        raise AnalysisDataConflictError(AnalysisDataAssessment('stale', 'Rerun analysis', [], [], 1))

    monkeypatch.setattr(CoverageService, 'require_current_results', reject)
    response = client.get('/api/analysis/42/export/latex')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'analysis_data_stale'


def test_source_endpoint_returns_zip_and_hides_internal_errors(export_client, monkeypatch):
    client, db = export_client
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        status='completed', muni_id=1, start_year=2019, end_year=2023, name='Example Town')
    monkeypatch.setattr(CoverageService, 'require_current_results', lambda *args: None)
    monkeypatch.setattr(PDFReportGenerator, 'generate_latex_archive', lambda *args: b'PK-source')
    response = client.get('/api/analysis/42/export/latex')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    assert '.zip' in response.headers['content-disposition']
    assert response.content == b'PK-source'

    def failed(*args):
        raise RuntimeError('private database credentials')

    monkeypatch.setattr(PDFReportGenerator, 'generate_latex_archive', failed)
    response = client.get('/api/analysis/42/export/latex')
    assert response.status_code == 500
    assert 'private' not in response.text


def test_source_endpoint_uses_export_rate_limit(export_client, monkeypatch):
    client, _ = export_client

    def exhausted(db, key, scope, limit, window):
        if scope == 'export':
            raise RequestLimitExceeded(17)

    monkeypatch.setattr(api_safeguards, 'consume_request_limit', exhausted)
    response = client.get('/api/analysis/42/export/latex')
    assert response.status_code == 429
    assert response.headers['retry-after'] == '17'
