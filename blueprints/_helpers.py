#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared helpers for Flask blueprints."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from flask import jsonify

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_LOOPBACK_IPS = {"127.0.0.1", "::1", "localhost"}

PDFXCHANGE_CANDIDATES = [
    r"C:\Program Files\Tracker Software\PDF Editor\PDFXEdit.exe",
    r"C:\Program Files\Tracker Software\PDF Viewer\PDFXCview.exe",
    r"C:\Program Files (x86)\Tracker Software\PDF Editor\PDFXEdit.exe",
]


def _is_loopback(remote: str) -> bool:
    if not remote:
        return False
    return remote in _LOOPBACK_IPS or remote.startswith("127.")


def _svc_response(result, status_code=None):
    """Convert service return values to a Flask JSON response."""
    if isinstance(result, tuple):
        data, code = result
        return jsonify(data), code
    return jsonify(result), (status_code or 200)


def _open_with_pdf_xchange(pdf_path):
    try:
        p = Path(pdf_path)
        if not p.exists():
            return False
    except Exception:
        return False
    exe = next((c for c in PDFXCHANGE_CANDIDATES if Path(c).exists()), None)
    try:
        if exe:
            subprocess.Popen([exe, str(p)], close_fds=True)
        else:
            os.startfile(str(p))
        return True
    except Exception:
        return False


def _launch_pdf_xchange(pdf_path):
    if _open_with_pdf_xchange(pdf_path):
        return {"success": True, "opened": True}, 200
    return {"success": False, "error": "PDFを開けませんでした"}, 500
