"""Compile application-owned LaTeX in a bounded, disposable workspace."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading


_COMPILERS = threading.BoundedSemaphore(2)


def compile_latex(source: str, assets: dict[str, bytes]) -> bytes:
    engine = shutil.which('pdflatex')
    if not engine:
        raise RuntimeError('PDF generation requires pdflatex; install the documented TeX packages.')
    for name in assets:
        if not re.fullmatch(r'[a-z][a-z0-9_-]*\.pdf', name) or name == 'report.pdf':
            raise ValueError('Invalid report asset filename')
    if not _COMPILERS.acquire(timeout=5):
        raise RuntimeError('PDF generation is busy. Please retry shortly.')
    try:
        with tempfile.TemporaryDirectory(prefix='nj-hin-report-') as directory:
            workspace = Path(directory)
            (workspace / 'report.tex').write_text(source, encoding='utf-8')
            for name, data in assets.items():
                (workspace / name).write_bytes(data)
            command = [engine, '-no-shell-escape', '-interaction=nonstopmode',
                       '-halt-on-error', '-file-line-error']
            if 'miktex' in engine.lower():
                command.append('--disable-installer')
            command.append('report.tex')
            env = dict(os.environ, openin_any='p', openout_any='p', TEXMFOUTPUT=directory)
            # Two passes resolve table/figure references; no shell or remote assets.
            with (workspace / 'compiler.log').open('wb') as log:
                for _ in range(2):
                    try:
                        result = subprocess.run(command, cwd=directory, env=env, timeout=60,
                                                stdin=subprocess.DEVNULL, stdout=log,
                                                stderr=subprocess.STDOUT, check=False)
                    except subprocess.TimeoutExpired as error:
                        raise RuntimeError('PDF typesetting timed out. Please retry.') from error
                    if result.returncode:
                        raise RuntimeError('PDF typesetting failed; check the installed TeX packages and report template.')
            target = workspace / 'report.pdf'
            if not target.is_file():
                raise RuntimeError('PDF typesetting did not produce a report.')
            data = target.read_bytes()
            if not data.startswith(b'%PDF-'):
                raise RuntimeError('PDF typesetting returned an invalid document.')
            return data
    finally:
        _COMPILERS.release()
