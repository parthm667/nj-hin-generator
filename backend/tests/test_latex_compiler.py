from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from app.services import latex_compiler


def test_compiles_twice_without_shell_escape_and_cleans_workspace(monkeypatch):
    monkeypatch.setattr(latex_compiler.shutil, 'which', lambda name: '/usr/bin/pdflatex')
    directories = []

    def run(command, **kwargs):
        directory = Path(kwargs['cwd'])
        directories.append(directory)
        assert '-no-shell-escape' in command
        assert kwargs.get('shell', False) is False
        assert kwargs['timeout'] <= 60
        assert (directory / 'network.pdf').read_bytes() == b'%PDF-map'
        assert (directory / 'report.tex').read_text(encoding='utf-8') == 'trusted template'
        (directory / 'report.pdf').write_bytes(b'%PDF-1.4\nresult')
        return CompletedProcess(command, 0)

    monkeypatch.setattr(latex_compiler.subprocess, 'run', run)
    assert latex_compiler.compile_latex('trusted template', {'network.pdf': b'%PDF-map'}).startswith(b'%PDF-')
    assert len(directories) == 2
    assert directories[0] == directories[1]
    assert not directories[0].exists()


def test_missing_engine_has_actionable_error(monkeypatch):
    monkeypatch.setattr(latex_compiler.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='pdflatex'):
        latex_compiler.compile_latex('source', {})


def test_timeout_is_bounded_and_temporary_files_are_removed(monkeypatch):
    monkeypatch.setattr(latex_compiler.shutil, 'which', lambda name: '/usr/bin/pdflatex')
    directories = []

    def run(command, **kwargs):
        directories.append(Path(kwargs['cwd']))
        raise TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(latex_compiler.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='timed out'):
        latex_compiler.compile_latex('source', {})
    assert not directories[0].exists()


def test_rejects_asset_paths_outside_workspace(monkeypatch):
    monkeypatch.setattr(latex_compiler.shutil, 'which', lambda name: '/usr/bin/pdflatex')
    with pytest.raises(ValueError, match='asset'):
        latex_compiler.compile_latex('source', {'../secret.pdf': b'bad'})
