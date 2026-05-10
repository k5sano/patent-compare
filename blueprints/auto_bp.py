#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Blueprint routes extracted from web.py."""
from __future__ import annotations

import json
import os
import re
import subprocess
import yaml
from pathlib import Path
from urllib.parse import urlencode
from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response
)

from services.case_service import (
    get_case_dir, load_case_meta, list_all_cases,
    load_json_file, find_citation_pdf,
)
from ._helpers import (
    PROJECT_ROOT, PDFXCHANGE_CANDIDATES, _launch_pdf_xchange,
    _open_with_pdf_xchange, _svc_response,
)

bp = Blueprint("auto", __name__)

@bp.route("/auto/run", methods=["POST"])
def auto_run():
    from services.auto_service import auto_run as _auto_run

    data = request.get_json() or {}
    case_ids = data.get("case_ids", [])
    steps = data.get("steps", [
        "keywords", "presearch", "download_citations", "compare", "excel"
    ])

    if not case_ids:
        return jsonify({"error": "案件が選択されていません"}), 400

    return Response(
        _auto_run(case_ids, steps),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
