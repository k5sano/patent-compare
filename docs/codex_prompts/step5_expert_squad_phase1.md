# Codex 依頼: Step 5 対比に「専門家集団」レイヤを段階導入 (Phase 1)

## 0. 前提と参照

このタスクの設計判断は以下にまとまっている。**実装前にこの 2 つを必ず読む**:

- `docs/design_review_llm_layer_2026-05-07.md` — レビュー全体 (LLM ルーティング、エラー診断改善、専門家レイヤ評価)
- `CLAUDE.md` — プロジェクト方針 (Web 自動化、LLM 呼出方針、既存 helper の優先利用)

ユーザ確定の構成は **A + 推奨案 + q**:
- **A** = 2-stage (`evidence_extractor` + `claim_chart_judge`)
- **推奨案** = extractor のみ (citation × requirement) 単位で並列、judge は citation 単位 1 回
- **q** = 環境変数 `COMPARE_MODE=expert_squad` で opt-in、デフォルトは現行の単一呼出パスのまま

## 1. 目的

Step 5「直接実行」(`compare_execute`) の品質を、既存パスを壊さずに改善する:

- Opus に長い公報全文を渡して迷わせる現状を、**安価モデル (Haiku/GLM-fast) による事前抽出**で解消
- (citation, 構成要件) 単位の並列で **wall time 短縮**
- 抽出された重点段落をマーカー付きで判定 prompt に注入し、**OCR ゆれ語 (グァニルシスティン等) も漏れ無く拾える**
- 既存の単一呼出パスは**そのまま残し**、`COMPARE_MODE=expert_squad` で opt-in

**やらないこと**: 3-stage / 4-stage chain、本願分析・KW 提案・チャットなど他工程の expert 化、UI 大幅変更、既存 prompt_generator の書き直し、`call_claude` の API 変更。

## 2. 実装範囲

### 2-1. 新規ファイル

#### `modules/llm_cache.py` (50〜70 行)

SHA256 ベースの file cache。`call_claude` のドロップイン代替として薄くラップする:

```python
def cached_call_claude(
    prompt_text: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    timeout: int = 600,
    use_search: bool = False,
    cache_scope: str = "case",        # "case" | "global" | "none"
    case_id: str | None = None,
    template_version: str = "v1",
) -> tuple[str, dict]:
    """call_claude を呼ぶ前に SHA256(prompt + model + effort + version) で
    cache lookup し、ヒットすれば LLM を呼ばずに返す。
    
    Returns: (response_text, meta)
        meta = {"cache_hit": bool, "cache_path": str, "model": str, "effort": str}
    
    cache_scope:
        - "case":   cases/<case_id>/llm_cache/<sha256>.json
        - "global": cases/_llm_cache_global/<sha256>.json (将来用)
        - "none":   cache 無効、毎回 call_claude
    """
```

仕様:
- cache file 形式: `{"prompt_sha256": str, "model": str, "effort": str, "template_version": str, "response": str, "saved_at": iso8601}`
- ヒット時は `meta["cache_hit"]=True` で return
- ミス時は call_claude を呼んで保存後 return
- `case_id` 必須 (scope=case 時)。未指定なら scope=none に降格

#### `modules/llm_experts.py` (180〜250 行)

Expert profile 定義 + `run_expert(...)` ランナー。

```python
from dataclasses import dataclass, field
from typing import Callable, Optional

@dataclass
class ExpertProfile:
    id: str
    role: str                              # ドキュメント用
    prompt_template: Callable              # (inputs: dict) -> str
    preferred_model: str                   # 'haiku' / 'opus' / 'glm-fast' 等
    fallback_model: Optional[str] = None
    effort: str = "high"
    validator: Optional[Callable] = None   # (parsed, context) -> list[str]
    output_format: str = "json"            # "json" | "text"
    cache_scope: str = "case"              # "case" | "global" | "none"
    template_version: str = "v1"
    timeout_sec: int = 600
    max_parallel: int = 1                  # 同時実行可能数 (provider レート対策)


@dataclass
class ExpertResult:
    parsed: dict | list | str | None       # 出力 (validator を通過したもの)
    raw: str                               # LLM の生応答
    cache_hit: bool
    model_used: str
    errors: list[str]                      # validator が見つけた問題 (空なら success)
    cost_estimate: float = 0.0             # token 課金推定 (簡易、now=0.0 でよい)


EXPERTS: dict[str, ExpertProfile] = {
    "evidence_extractor": ExpertProfile(
        id="evidence_extractor",
        role="引用文献から特定の構成要件に関係する段落・表を抽出する専門家",
        prompt_template=_build_evidence_extractor_prompt,  # 後述
        preferred_model="haiku",
        fallback_model="glm-haiku",
        effort="medium",
        validator=_validate_evidence_extractor_output,
        output_format="json",
        cache_scope="case",
        template_version="v1",
        timeout_sec=180,
        max_parallel=4,
    ),
    "claim_chart_judge": ExpertProfile(
        id="claim_chart_judge",
        role="重点段落と構成要件をもとに開示有無を判定する審査官専門家",
        prompt_template=_build_claim_chart_judge_prompt,   # 後述
        preferred_model="opus",
        fallback_model="sonnet",
        effort="high",
        validator=_validate_claim_chart_judge_output,
        output_format="json",
        cache_scope="case",
        template_version="v1",
        timeout_sec=600,
        max_parallel=1,
    ),
}


def run_expert(
    expert_id: str,
    *,
    inputs: dict,
    case_id: str | None = None,
    model_override: str | None = None,
    effort_override: str | None = None,
    skip_cache: bool = False,
) -> ExpertResult:
    """profile を見て model 解決 → cached_call_claude → validator → ExpertResult。
    例外は投げず errors リストに格納する (UI 描画しやすくするため)。
    """
```

仕様:
- `prompt_template(inputs)` で文字列を作る (テンプレ関数は同モジュール内に定義)
- `model_override` が来たら profile の preferred_model に勝つ
- LLM 呼出失敗時は `fallback_model` で 1 回だけリトライ (preferred と fallback が同 provider でなければ意味あり)
- `validator(parsed, inputs)` が errors リストを返したら parsed=None で errors を埋める
- 例外を一切外に投げない設計 (`ExpertResult.errors` で表現)

#### Expert 1: `evidence_extractor`

**入力**: `{"requirement_id": str, "requirement_text": str, "keywords": [str], "citation_id": str, "citation_text": str}`

**出力 JSON**:
```json
{
  "requirement_id": "1A",
  "citation_id": "JP2014-001183",
  "evidence_paragraphs": [
    {"paragraph_id": "0035", "snippet": "...原文200字以内...", "score": 0.9, "reason": "成分Aが明示"},
    ...
  ],
  "evidence_tables": [
    {"table_id": "表1", "snippet": "...", "score": 0.7, "reason": "実施例配合"},
    ...
  ],
  "no_match_reason": null
}
```

`no_match_reason` は段落が見つからない時のみ短い理由を入れる (例: `"成分名は記載なし、上位概念のみ"`)。

**プロンプトのポイント**:
- 「あなたは引用文献から該当箇所を抽出する専門家です。判定はしないでください」を明確化
- キーワードは正規化 (`グアニルシステイン` と書いても OCR で `グァニルシスティン` 等の表記揺れも対象、と prompt に明記)
- 結果は最大 5 段落 + 3 表まで (long-tail を切る)
- 抽出のみで判断は次段に任せる

**validator 仕様** (`_validate_evidence_extractor_output(parsed, inputs)`):
- `requirement_id` が `inputs["requirement_id"]` と一致するか
- `evidence_paragraphs` の各 `paragraph_id` が citation の paragraphs に実在するか
- `score` が 0〜1 範囲か
- 違反は errors に 1 行ずつ追加して返す (raise しない)

#### Expert 2: `claim_chart_judge`

**入力**:
```python
{
    "case_id": str,
    "segments": list,       # 全構成要件
    "citations": list,      # 1 件 or 複数
    "keywords": list,
    "field": str,
    "hongan": dict,
    "evidence_by_req_cit": dict,  # {(req_id, cit_id): evidence_extractor の出力}
}
```

**プロンプト構築方針**:
- 既存 `prompt_generator.generate_prompt_requirement_first` を**そのまま再利用**
- ただし各 (citation, requirement) ブロックに「**重点参酌**」セクションを追加挿入 (extractor の `evidence_paragraphs` から)
- 既存の output format 指定もそのまま継承 (parse_response が読める形)
- `template_version="v1"` (今後変えたら "v2"、cache miss 強制)

`generate_prompt_requirement_first` を改造せず、**ラッパ関数 `_build_claim_chart_judge_prompt` を新設**して、生成された prompt 文字列に重点参酌マーカーを差し込む形が安全。

**validator 仕様**: 既存 `modules.response_parser.parse_response` をそのまま使う。返り値が None なら errors に「parse failed」を追加。

### 2-2. 既存ファイル変更

#### `services/comparison_service.py` の `compare_execute`

冒頭で `os.environ.get("COMPARE_MODE")` を読む:

```python
if os.environ.get("COMPARE_MODE", "").strip().lower() == "expert_squad":
    return _compare_execute_expert_squad(
        case_id, citation_ids, model=model, mode=mode, effort=effort,
    )
# 以下、既存コードはそのまま
```

`_compare_execute_expert_squad(...)` を同ファイル末尾に新設:

```python
def _compare_execute_expert_squad(case_id, citation_ids, *, model, mode, effort):
    """expert squad 経路 (Phase 1: 2-stage):
       1. 各 (citation, requirement) を evidence_extractor で並列抽出
       2. 全 citation を claim_chart_judge で 1 回判定 (extractor 結果を重点段落として注入)
       3. 既存と同じ responses/<id>.json を保存
    """
    from modules.llm_experts import run_expert, EXPERTS
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # ...
```

実装メモ:
- citations / segments / keywords / hongan のロード処理は既存 `_load_citation_for_prompt` を流用
- evidence_extractor は ThreadPoolExecutor (max_workers=4 程度) で並列
- 各タスク失敗時はその (citation, requirement) を `evidence_paragraphs=[]` 扱いにして judge 段階に進む (フェールセーフ)
- judge の結果は既存 `parse_response` → `_resolve_doc_id` → `_normalize_cited_locations_inplace` を踏襲して保存
- レスポンスに `expert_squad_meta` を追加: 各段の `cache_hit`, `model_used`, `errors` の集計

UI には既存の `success / saved_docs / num_docs / mode_used` をそのまま返す (互換維持)。`expert_squad_meta` は追加情報として乗せる程度でよい。

### 2-3. テスト

#### `tests/test_llm_cache.py` (~80 行)
- ミスとヒットの基本ケース
- model 違い → 別キャッシュ
- template_version 違い → 別キャッシュ
- scope=none で cache 無効化
- 不正な JSON cache file は無視して再実行 (壊れたキャッシュへの耐性)

#### `tests/test_llm_experts.py` (~120 行)
- `evidence_extractor`: monkey patch で fake call_claude を入れて prompt の入出力を検証
- `claim_chart_judge`: 同じく monkey patch
- validator がエラーを検出するケース (paragraph_id 不一致、score 範囲外など)
- model_override が効くこと
- fallback_model に切り替わること (preferred 失敗時)
- ExpertResult.cache_hit が True/False で正しく返ること

#### `tests/test_compare_execute_expert_squad.py` (~120 行)
- `COMPARE_MODE=expert_squad` 経路でも既存と同じ `responses/<id>.json` が保存される
- citation 1 件 + 構成要件 3 件で 3 並列 extractor が走り、judge が 1 回呼ばれること (Counter で確認)
- evidence_extractor が 1 件失敗しても judge までは進む
- `COMPARE_MODE` 未設定時は既存 `compare_execute` 経路 (回帰テスト)
- `expert_squad_meta` が response に含まれること

すべて `monkeypatch` で `call_claude` を fake に差し替える (実 LLM 呼ばない)。

## 3. 設計上の制約 (必ず守る)

1. **既存の `compare_execute` を破壊しない**。`COMPARE_MODE` が `expert_squad` でない限り**完全に既存挙動**。
2. **`call_claude` の API は変えない**。新レイヤは上に乗せる。
3. **`prompt_generator.generate_prompt_requirement_first` を改造しない**。既存 prompt 文字列にマーカーを後付けで挿入するラッパ方式。
4. **`response_parser.parse_response` を再利用**。新パーサを書かない。
5. **`_resolve_doc_id` / `_normalize_cited_locations_inplace` も再利用**。
6. **既存テスト 498 件を 1 件も壊さない** (`python -m pytest -q` を最後に確認)。
7. **CLAUDE.md の方針に従う**: 既存 `requests` 使用 (新規 httpx 導入禁止)、`tools/recon.py` で偵察するような外部通信は不要 (このタスクは LLM 呼出のみ)、cache 場所は `cases/<case_id>/llm_cache/` で他のキャッシュと整合。

## 4. やらないでほしいこと (脱線禁止)

- **Phase 2 以降** (3-stage chain、`legal_reviewer`、`json_normalizer` など) は今回禁止
- 他工程 (本願分析、KW 提案、チャット、Step 4.5 AI スコア) の expert 化は禁止
- `MODEL_ALIASES` の修正・追加は禁止 (LLM ルーティング側は今回触らない)
- UI (case.html / case.js) への変更は禁止 (UI からは既存 `compare_execute` を呼び続けるだけ。expert_squad 経路は環境変数で発動)
- 設計書・仕様書類のリッチ化は禁止 (このプロンプト + 各ファイルの docstring で十分)
- 17 expert を一気に列挙する作業は禁止 (今回は 2 expert だけ)

## 5. 完了の定義 (DoD)

すべて満たすこと:

1. `modules/llm_cache.py` / `modules/llm_experts.py` が新設されている
2. `services/comparison_service.py` に `_compare_execute_expert_squad` が追加され、`COMPARE_MODE` 分岐が冒頭に入っている
3. テスト 3 ファイル (`test_llm_cache.py`, `test_llm_experts.py`, `test_compare_execute_expert_squad.py`) が追加され、`pytest -q` でグリーン
4. 既存テスト 498 件を 1 件も壊していない (合計 600〜650 件パス想定)
5. `COMPARE_MODE=expert_squad python web.py` で起動 → 任意の案件 (citation 登録済) で「直接実行」を 1 回実行 → 既存と同じ形式の `responses/<id>.json` が保存され、Step 6 が通常表示できることを smoke 確認
6. `COMPARE_MODE` 未設定 (既定) で起動 → 既存と同じ動作 (回帰テストで担保)
7. コミットメッセージは feat(step5): 対比に専門家集団 Phase 1 (extractor + judge, opt-in)
   - 中身: 何ができたか / どこを既存から再利用したか / opt-in 方法 / 既存挙動非破壊の旨
8. 1 コミットで push (feature ブランチではなく master 直)

## 6. 既存コードの再利用ポイント (重要)

- `modules.claude_client.call_claude` (LLM 呼出本体)
- `modules.claude_client.resolve_model` / `model_provider` (model alias 解決)
- `services.search_run_service._normalize_text_for_match` (OCR 正規化、extractor の prompt に「正規化して照合」と書く時の参考)
- `services.comparison_service._load_citation_for_prompt` (citation + hit_text マージ)
- `services.comparison_service._is_empty_citation` (空 citation 検出)
- `services.comparison_service._resolve_doc_id` (LLM 応答 ID 揺れ吸収)
- `services.comparison_service._normalize_cited_locations_inplace` (cited_location 正規化)
- `modules.prompt_generator.generate_prompt_requirement_first` (judge 用 prompt の土台、改造禁止)
- `modules.response_parser.parse_response` (judge の validator として使う)

## 7. 開発フローの推奨

1. ブランチは切らない (master 直、既存と同じ運用)
2. **テスト先行**: まず `test_llm_cache.py` を書いて RED → 実装 → GREEN
3. 次に `test_llm_experts.py` で extractor / judge の入出力を fake で固定 → 実装
4. 最後に `test_compare_execute_expert_squad.py` で全体結合
5. 既存 `pytest -q` を実装の合間にも回して 498 件を割らないことを確認
6. smoke 確認 (`COMPARE_MODE=expert_squad` で 1 案件) → コミット → push

## 8. 質問があれば

不明点 (例: validator の挙動、cache file の暗号化、provider 別 timeout の扱い) があれば実装を止めて質問してから進めること。仕様書外の判断はユーザに確認する。

---

**目安規模**: 新規 約 350〜450 行 (実装) + 約 300〜400 行 (テスト) + コミット 1 件
**期待成果**: Opus prompt 30〜50% 削減 + wall time 30% 短縮 + OCR ゆれ語の捕捉精度向上、すべて opt-in で既存挙動非破壊。
