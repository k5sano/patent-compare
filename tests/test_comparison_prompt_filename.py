#!/usr/bin/env python
# -*- coding: utf-8 -*-

from services.comparison_service import _safe_prompt_filename


def test_safe_prompt_filename_shortens_long_citation_list():
    label = "_".join(f"JP{i:04d}-123456" for i in range(30))
    name = _safe_prompt_filename(label)

    assert name.endswith("_prompt.txt")
    assert len(name) < 120
    assert name != f"{label}_prompt.txt"


def test_safe_prompt_filename_replaces_windows_forbidden_chars():
    name = _safe_prompt_filename('JP/2020:001*ABC?')

    assert name == "JP_2020_001_ABC_prompt.txt"
