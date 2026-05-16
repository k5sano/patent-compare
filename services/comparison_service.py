#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比分析・Excel出力・PDF注釈サービスの互換ファサード。

実装は services.comparison.* に分割している。既存の import パスを壊さないため、
このモジュールは公開関数と一部テスト対象 helper を再エクスポートする。
"""

from services.comparison.annotation import annotate_all_citations, annotate_citation
from services.comparison.common import (
    _backup_existing_annotated_pdf,
    _enrich_citation_with_hit_text,
    _load_citation_for_prompt,
    _safe_prompt_filename,
    _write_annotated_pdf,
)
from services.comparison.execute import compare_execute, get_comparison_progress
from services.comparison.export import export_excel, export_full_report
from services.comparison.inventive import (
    inventive_step_execute,
    inventive_step_prompt,
    inventive_step_response,
)
from services.comparison.prompt import (
    _canonical_digits,
    _digit_groups,
    _empty_citation_error,
    _filter_keywords_by_valid_segments,
    _get_all_segment_ids,
    _is_empty_citation,
    _normalize_doc_id,
    _resolve_doc_id,
    check_segments_freshness,
    generate_prompt_multi,
    generate_prompt_single,
)
from services.comparison.response import (
    _decorate_comparison_with_notation,
    _normalize_cited_locations_inplace,
    get_response,
    prune_orphan_comparisons,
    save_response_multi,
    save_response_single,
    update_comparison_cell,
)
from services.comparison_chat_service import (
    apply_judgment_override,
    build_cell_context,
    chat_cell,
    get_cell_chat_history,
    list_unmet_cells,
)

__all__ = [
    "annotate_all_citations",
    "annotate_citation",
    "check_segments_freshness",
    "compare_execute",
    "get_comparison_progress",
    "export_excel",
    "export_full_report",
    "generate_prompt_multi",
    "generate_prompt_single",
    "get_response",
    "inventive_step_execute",
    "inventive_step_prompt",
    "inventive_step_response",
    "prune_orphan_comparisons",
    "save_response_multi",
    "save_response_single",
    "update_comparison_cell",
    "apply_judgment_override",
    "build_cell_context",
    "chat_cell",
    "get_cell_chat_history",
    "list_unmet_cells",
    "_backup_existing_annotated_pdf",
    "_canonical_digits",
    "_decorate_comparison_with_notation",
    "_digit_groups",
    "_empty_citation_error",
    "_enrich_citation_with_hit_text",
    "_filter_keywords_by_valid_segments",
    "_get_all_segment_ids",
    "_is_empty_citation",
    "_load_citation_for_prompt",
    "_normalize_cited_locations_inplace",
    "_normalize_doc_id",
    "_resolve_doc_id",
    "_safe_prompt_filename",
    "_write_annotated_pdf",
]
