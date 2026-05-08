# LLM レイヤ 設計レビュー (2026-05-07)

直近セッションで実施した 3 つのレビューをまとめる。実装はまだしていない。

1. LLM モデル切替まわり (Claude / Codex / GLM ルーティング) のレビュー
2. Step 5「直接実行」のエラー診断改善案 (最小変更案)
3. ユーザ提案「専門家部隊レイヤ」の設計評価

---

## 0. 経緯と前提

- ユーザー報告: Step 5「直接実行」(GLM-5-Turbo / requirement_first / effort=high) で「色々エラーが出る」
- 直前の Codex コミット 2 件:
  - `7c0fe90` Step 5 keyword + WO location handling
  - `d389991` LLM provider routing (Claude/Codex/GLM 切替)
- 私 (Claude) の初動レビューでは「**MODEL_ALIASES の GLM 系が架空**」を最有力原因と推測。
  → ユーザの追加情報 **「GLM のモデル名は問題ない。動いている」** で誤りと確定。訂正済。
- 実エラー本文 (alert の `data.error`、`responses/_last_raw_response.txt`、Flask コンソール) は未取得。
  これがあれば本ドキュメントの修正候補は 5〜10 行に絞り込める可能性あり。

---

## 1. LLM モデル切替まわり レビュー

### A. ルーティング (`modules/claude_client.py`)

#### [HIGH] 未知エイリアスがそのまま CLI/API に渡る
- `MODEL_ALIASES.get(name, name)` ([line 213](../modules/claude_client.py#L213))
- タイポ・未知モデルもサーバ側で reject されない → 後段の API/CLI エラーまで進んで失敗
- サニティチェック (whitelist 検証) なし

#### [MEDIUM] `_glm_thinking` がモデル別非対応を考慮しない
- [line 335-339](../modules/claude_client.py#L335-L339): effort ≥ medium で常に `payload["thinking"]={"type":"enabled"}`
- `glm-4.5-air` / `glm-4.5-flash` / `glm-4-flash` 系は thinking 非対応 → 400 を返す可能性
- 推奨: `THINKING_CAPABLE_MODELS = {...}` を明示

#### [MEDIUM] `max_tokens=32768` ハードコード
- [line 354](../modules/claude_client.py#L354)
- モデル別出力上限を考慮していない (`glm-4.5-flash` 8K、`glm-4-flash` 4K 等)
- 推奨: モデル別 default を持つか、フィールド削除して API 既定に委ねる

#### [MEDIUM] base_url パスマッチが脆弱
- [line 349-350](../modules/claude_client.py#L349-L350)
- `rstrip("/")` 後に `/chat/completions` 末尾チェック。微妙なエッジケース残

#### [LOW] Codex CLI に `effort` フラグが渡らない
- [line 287-294](../modules/claude_client.py#L287-L294): `--effort` flag が無い
- Claude CLI 専用の概念だが、Codex の reasoning モード相当に対応するなら別実装必要

#### [LOW] Codex モデル名が新しすぎる可能性
- `gpt-5.5` `gpt-5.4` 等。Codex CLI version によっては未対応で `--list-models` 確認していない

### B. Windows 環境変数読み取り (`_load_windows_registered_env`)

#### [MEDIUM] 環境変数 vs registry の優先順序がトリッキー
- [line 88-110](../modules/claude_client.py#L88-L110): `os.environ` → registry → `config.yaml`
- `os.environ` に古い値が残るとデバッグ難 (どこから値が来たか不明)
- 推奨: 読み込み元 (env/registry/config.yaml) をログ出力

#### [MEDIUM] Performance: registry 走査のキャッシュなし
- [line 48-65](../modules/claude_client.py#L48-L65): 2 階層 × 全 entry 走査
- `is_glm_available()` → `_load_zai_api_key()` → registry スキャン というチェーンが `/api/claude-status` 等で毎回発火
- 推奨: 30 秒程度の TTL キャッシュ

#### [LOW] `os.path.expandvars` の副作用
- API キーに `%FOO%` が含まれると展開される (常識的には問題なし)

### C. Step 5 直接実行 (`compare_execute` + `executeCompare`)

#### [HIGH] timeout が provider 中立でない
- [comparison_service.py:1389](../services/comparison_service.py#L1389): `timeout = 600 if len(citations) <= 2 else 900`
- Claude CLI: 600s で十分 / Codex: 900s 必要 / **GLM thinking enabled: 1200s+ あり得る**
- 「色々エラー」の 1 つはタイムアウトの可能性
- 推奨: provider 別 default + UI で延長可能に

#### [HIGH] 並列判定が雑
- [comparison_service.py:1366-1371](../services/comparison_service.py#L1366-L1371):
  ```python
  is_lightweight = ("sonnet" in model_l) or ("haiku" in model_l) or ("mini" in model_l) or ("glm" in model_l)
  ```
- 全 GLM が並列向き判定。`glm-opus` (重) や API レート制限 (429) を考慮せず
- 推奨: GLM は max_workers を 2 で頭打ち、または並列無効化

#### [MEDIUM] `mode="requirement_first"` フォールバックがサイレント
- [line 1342-1347](../services/comparison_service.py#L1342-L1347)
- keywords 無しで legacy 移行、`fallback_to_legacy=True` でレスポンス返却するが UI 未表示
- ユーザは品質劣化に気付けない

### D. JS model-picker (`static/js/case.js`)

#### [HIGH] `getPickerModel` の disabled fallback が壊れる
- [line 124-129](../static/js/case.js#L124-L129)
- fallback `'sonnet'` 固定。Claude が利用不可な環境では disabled な値が server に送信
- 推奨: 有効な先頭オプションを動的に fallback

#### [MEDIUM] `_LLM_STATUS` をリフレッシュする手段なし
- [line 4763-4787](../static/js/case.js#L4763-L4787): ページロード時 1 回のみ
- API キー設定後にブラウザ更新しないと picker は disabled のまま

### E. UI エラー表示

#### [HIGH] `alert(data.error)` 一辺倒で原因区別なし
- Claude CLI 未インストール / Codex CLI 未ログイン / GLM API キー未設定 / モデル未存在 / 429 / タイムアウト / JSON パース失敗
- 全部同列 (alert に長文表示)。ユーザが切り分けにくい

#### [MEDIUM] パース失敗時の参照案内が薄い
- `responses/_last_raw_response.txt` の参照リンクが UI に無い

---

## 2. Step 5 エラー診断 改善案 (最小変更)

### 設計目標

1. ユーザが「APIキー未設定」「モデル未利用」「タイムアウト」「LLM応答パース失敗」を**カテゴリで区別**
2. サーバログに**構造化された原因記録**
3. 既存 UI を**大きく変えない**

### 変更案 (合計 ~95 行)

#### S-1. server: 例外分類を `phase` フィールドで明示 (~30 行)

`comparison_service.compare_execute` の `except` を細分化:

```python
provider = model_provider(model)
try:
    raw_response = call_claude(prompt_text, **call_kwargs)
except ClaudeNotFoundError as e:
    logger.warning("Step5 [%s] LLM 未利用: %s", provider, e)
    return {
        "error": str(e), "phase": "llm_not_available",
        "provider": provider, "model": model,
        "hint": _provider_setup_hint(provider),
    }, 502
except ClaudeTimeoutError as e:
    logger.warning("Step5 [%s] タイムアウト (%ds, prompt=%d文字)",
                   provider, timeout, len(prompt_text))
    return {
        "error": str(e), "phase": "llm_timeout",
        "provider": provider, "model": model, "timeout_sec": timeout,
        "prompt_chars": len(prompt_text),
        "hint": "より軽量なモデルを試す / プロンプトを短く / "
                "環境変数 COMPARE_TIMEOUT で延長 (将来対応)",
    }, 504
except ClaudeExecutionError as e:
    logger.warning("Step5 [%s/%s] 実行エラー: %s", provider, model, e)
    return {
        "error": str(e), "phase": "llm_execution",
        "provider": provider, "model": model,
        "hint": _execution_error_hint(provider, str(e)),
    }, 502
except ClaudeClientError as e:
    logger.warning("Step5 [%s] 不明エラー: %s", provider, e)
    return {"error": str(e), "phase": "llm_unknown",
            "provider": provider, "model": model}, 502
```

#### S-2. `claude_client.py` に hint helper (~20 行)

```python
def provider_setup_hint(provider):
    if provider == "glm":
        return ("z.ai (BigModel) の API キーが必要です。環境変数 ZAI_API_KEY か "
                "config.yaml の zai_api_key に設定し、Flask を再起動してください。")
    if provider == "codex":
        return ("Codex CLI のインストール + ChatGPT ログインが必要です。"
                "`codex login` で認証してください。")
    return ("Claude Code CLI のインストールと OAuth 認証が必要です。"
            "`claude --version` で確認してください。")


def execution_error_hint(provider, error_text):
    et = (error_text or "").lower()
    if "model" in et and ("not found" in et or "not_found" in et or "404" in et):
        return ("モデル名がプロバイダで未提供の可能性。"
                "MODEL_ALIASES の設定を確認してください。")
    if "rate" in et or "429" in et:
        return "レート制限に到達しました。数分待ってから再実行してください。"
    if "401" in et or "unauthorized" in et:
        return "API キーが無効か期限切れです。"
    if "context" in et or "token" in et:
        return "プロンプトが長すぎます。citation 数を減らすか、より長いコンテキストのモデルを選んでください。"
    return ""
```

#### S-3. `_call_glm_chat` の HTTP コード細分化 (~10 行)

```python
if resp.status_code == 401:
    raise ClaudeNotFoundError("GLM API キーが無効です (401)")
if resp.status_code == 404 or "model_not_found" in resp.text.lower():
    raise ClaudeExecutionError(
        f"GLM モデル '{model}' が見つかりません (z.ai で実在するか確認): {resp.text[:200]}"
    )
if resp.status_code == 429:
    raise ClaudeExecutionError(f"GLM レート制限 (429): {resp.text[:200]}")
if resp.status_code >= 400:
    raise ClaudeExecutionError(f"GLM API エラー {resp.status_code}: {resp.text[:500]}")
```

#### S-4. パース失敗を `phase=parse_failed` で明示 (~10 行)

```python
result, parse_errors = parse_response(raw_response, all_segment_ids)
if not result:
    return {
        "error": "LLM 応答が JSON として解釈できませんでした",
        "phase": "parse_failed",
        "raw_preview": raw_response[:300],
        "raw_path": str(raw_path.relative_to(PROJECT_ROOT)),
        "model": model, "provider": provider,
        "hint": "responses/_last_raw_response.txt を確認 / 別モデルで再試行",
    }, 502
```

#### S-5. JS: inline error バナーへ移行 (~30 行)

`alert()` を `parse-result` panel への inline 描画に変更。`phase` で色とアイコンを切替:

```js
function _renderExecError(targetEl, data) {
  const palette = {
    llm_not_available: {bg:'#422006', fg:'#fbbf24', icon:'⚠', label:'LLM 未利用'},
    llm_timeout:       {bg:'#450a0a', fg:'#fca5a5', icon:'⏱', label:'タイムアウト'},
    llm_execution:     {bg:'#450a0a', fg:'#fca5a5', icon:'✗', label:'LLM 実行エラー'},
    parse_failed:      {bg:'#422006', fg:'#fbbf24', icon:'?', label:'応答パース失敗'},
  };
  const p = palette[data.phase] || {bg:'#450a0a', fg:'#fca5a5', icon:'!', label:'エラー'};
  const meta = [data.provider, data.model].filter(Boolean).join(' / ');
  targetEl.innerHTML = `
    <div style="padding:0.8rem 1rem; background:${p.bg}; color:${p.fg}; border-radius:8px;">
      <strong>${p.icon} ${p.label}${meta ? ` (${meta})` : ''}</strong>
      <div style="margin-top:0.3rem; font-size:0.9rem;">${_escapeHtml(data.error)}</div>
      ${data.hint ? `<div style="margin-top:0.3rem; font-size:0.82rem; opacity:0.85;">→ ${_escapeHtml(data.hint)}</div>` : ''}
      ${data.raw_path ? `<div style="margin-top:0.3rem; font-size:0.78rem;"><code>${_escapeHtml(data.raw_path)}</code> を確認</div>` : ''}
    </div>`;
}
```

#### S-6. (任意) 起動時セルフチェックログ (~5 行)

`web.py` の起動部:
```python
from modules.claude_client import llm_status as _llm_status
_st = _llm_status()
print(f"[INFO] LLM: claude={_st['claude_available']} "
      f"codex={_st['codex_available']} glm={_st['glm_available']}")
```

### 既存挙動への影響

| 影響 | 範囲 | 対策 |
|---|---|---|
| ステータスコード変更 (timeout: 502→504) | 古いキャッシュ済 JS | `if (!resp.ok)` だけなら影響なし |
| `phase` フィールド追加 | 既存レスポンス | 加算のみ、破壊しない |
| alert → inline | UX 変化 | alert はパース失敗時のみ fallback として残す |

---

## 3. ユーザ提案「専門家部隊レイヤ」評価

### 全体評価: ★★★☆☆ (3/5) — 方向は◯、範囲は要削減

「専門家プロファイル + Orchestrator」は正しい問題意識だが、**17 専門家・4段階チェイン・Phase 5 まで一気通貫**は CLAUDE.md「欲張らず、軽やかに」から逸脱。**Phase 1〜3 まで縮小+段階導入**が推奨。

### 強み (採用すべき発想)

| 強み | 現状の課題 | 提案の解決 |
|---|---|---|
| モデル/Effort/Validator/Cache 集約 | 各 service が直接 call_claude | `LLMTask` 発行のみ |
| キャッシュ層 | 全くない | SHA256 file cache で再実行ほぼ無料 |
| Validator 標準化 | parse 失敗が silent | profile に validator |
| Expert role 分業 | 1 プロンプトに全部 | per requirement 並列化 |
| Tiered model 戦略 | Step 4.5 でも Opus | 一次は GLM-fast/Haiku |

### リスクと懸念

#### [HIGH] 抽象化が早すぎる可能性
- 現在 `call_claude` callsite は 9 箇所。閾値 (20+) 未達
- 17 expert profile + schema + validator + test = 約 1700 行の維持コスト

#### [HIGH] `default_model + fast_model` の二択は次元不足
- 実態は 3 プロバイダ × 5 effort × 2-4 モデル = 60+ 組合せ
- UI の上書き経路必須
- 推奨: `preferred_model + fallback_model` (失敗時)

#### [HIGH] Step 5 の 4-stage chain は費用爆発リスク
- 現状: citation × 1 LLM 呼出 → 提案後: citation × 4 = 4 倍コスト + latency
- 推奨: Phase 5 として保留、デフォルトは現状 1-stage 維持

#### [MEDIUM] 17 expert profile は過剰
- 即座に有用なのは 4 つ (claim_interpreter / evidence_extractor / claim_chart_judge / screening_reviewer)
- 残り 13 は現状 Claude 1 本で十分動いている領域

#### [MEDIUM] `output_schema` / `validator` 仕様が曖昧
- 推奨: `validator` は callable 参照、`output_schema` は廃止 (response_parser 再利用)

#### [MEDIUM] `cache_key_inputs` 不明確
- 推奨キー: `(prompt_text_sha256, model_resolved, effort, prompt_template_version)`
- template_version は手動 bump で「強制再実行」を許す

#### [MEDIUM] 既存資産の再利用が見えない
- `response_parser.parse_response` (Step 5 validator として最強)
- `_resolve_doc_id` (LLM 応答 ID 揺れ吸収)
- `_normalize_cited_locations_inplace` (cited_location 正規化)
- これらをそのまま expert chain の各段に組み込み可能

#### [LOW] Cache のスコープ
- `screening_reviewer` は case 横断で共有すべき (同公報を別案件でも評価)
- profile ごとに `cache_scope: "case" | "global"` を明示

### 推奨段階的プラン (5 Phase → 3 Phase に縮約)

#### Phase 1 — 薄い orchestrator + キャッシュ (2〜3 日)

**目的**: 既存挙動を破壊せず、cache + validator を獲得。

```
modules/llm_cache.py (~50 行)
  cached_call_claude(prompt, model, effort, *, scope, case_id, template_version)

modules/llm_experts.py (~120 行)
  EXPERTS = {"screening_reviewer": {...}}  # まず 1 個だけ
  run_expert(expert_id, prompt, *, model_override, case_id, **inputs) -> dict
```

**移行先 1 件**: `services/search_run_service.py` の AI スコアリング (Step 4.5)。
- case 横断キャッシュ可能 + Haiku/GLM-fast 適性 + 量が多い

**保留**: Step 5 / Step 6 / hongan_analysis / chat (call_claude 直呼びのまま)

#### Phase 2 — Step 5 evidence pre-filter (1 週間)

**目的**: Opus に長文を渡す前に、安価モデルで段落絞り込み (4-stage chain ではなく **2-stage**)。

```
EXPERTS["evidence_extractor"] = {
    "preferred_model": "haiku",  # or "glm-haiku"
    "effort": "low",
    "cache_scope": "case",  # citation × requirement 単位
}
```

`compare_execute` の流れ:
1. (新) 各 (citation, requirement) ペアで `evidence_extractor` を並列実行
2. (既存) Opus が判定 (重点段落マーカー付き prompt)

**やらないこと**: claim_chart_judge / legal_reviewer の独立化 (1 つの Opus 呼出で判定+セルフレビューさせる方が現状品質に近く、デバッグしやすい)

#### Phase 3 — 移行範囲拡大 (1〜2 週間)
- keyword_recommender → expert (terminology + synonym)
- hongan_analysis_service → expert (claim_interpreter + spec_reader)
- chat は最後 (UX 影響最大)

各移行で「expert 化前後で出力が同じ」回帰テストを必ず付ける。

#### Phase 4 以降 (将来)
- claim_chart_judge / legal_reviewer の独立化 (1-stage 判定が品質的に不足してきた時)
- 表抽出を画像対応 expert に
- 進歩性判断の panel 化

### API 設計の具体修正案

#### profile スキーマ
```python
@dataclass
class ExpertProfile:
    id: str
    role: str
    prompt_template: Callable          # (inputs) → str
    preferred_model: str               # alias 可
    fallback_model: Optional[str] = None
    effort: str = "high"
    validator: Optional[Callable] = None  # (parsed, context) → list[str]
    output_format: str = "json"
    cache_scope: str = "case"          # "case" | "global" | "none"
    template_version: str = "v1"
    timeout_sec: int = 600
    max_parallel: int = 1
```

#### 呼び出しシグネチャ
```python
def run_expert(
    expert_id: str,
    *,
    inputs: dict,
    case_id: Optional[str] = None,
    model_override: Optional[str] = None,
    effort_override: Optional[str] = None,
    skip_cache: bool = False,
) -> ExpertResult:
    """ExpertResult = (parsed, raw, hits_cache, cost_estimate, errors)
    例外は投げず errors リストに格納。
    """
```

#### 既存 `call_claude` との関係
```
[各 service] ─→ run_expert(expert_id, ...)            # 推奨経路
                ↓
            cached_call_claude(prompt, model, ...)
                ↓
            call_claude(prompt, model, effort, ...)   # 既存、Codex/Claude/GLM ルーティング
```

expert 層は cache の上、call_claude の上の **新しい 1 レイヤ**。call_claude の API は変えない。

### CLAUDE.md との整合性

| CLAUDE.md 原則 | 提案との整合性 |
|---|---|
| 「欲張らず、軽やかに」 | 17 expert は欲張りすぎ。4 expert で十分 |
| 「既存資産を尊重」 | response_parser / prompt_generator / claude_client を再利用するならOK |
| 「過剰な抽象化を避ける」 | 段階導入なら可。一気通貫 NG |
| 「テストで挙動を担保」 | profile 化で回帰テストが書きやすくなる側面はあり |

---

## 4. 推奨進行 (このドキュメント直後に手をつけるなら)

### 即決すべきこと

1. **Phase 1 (cache + 4 expert profile + screening_reviewer 1 件移行) から始める** — リスク最低・リターンが見える
2. **Step 5 の 4-stage chain は Phase 4 以降に保留** — まず evidence_extractor 1 段だけ
3. **`fast_model` ではなく `preferred_model + fallback_model`** — 次元を増やさない
4. **`validator` は callable** — `output_schema: string` は採用しない
5. **既存 `response_parser.parse_response` を Step 5 expert の validator として再利用**

### 保留すべきこと
- 17 expert を最初に列挙する設計書を書くこと
- `output_schema` の DSL 設計
- expert chain (evidence → judge → review) の最初からの導入

### 順序候補

A. **エラー診断改善 (S-1〜S-5、95 行) を先に実装** → 次に Step 5 でエラーが起きた時に切り分けやすくしておく
B. **Phase 1 の expert layer + cache (200〜250 行) から実装** → screening_reviewer (Step 4.5 AI スコア) を移行して効果測定
C. **A → B の順** (推奨)

C. が安全。エラー診断を先に入れることで、expert layer 移行時のトラブル切り分けにも役立つ。

---

## 5. 補足: Codex への依頼候補 (本ドキュメント外)

CLAUDE.md `偵察フェーズ` 流派と Codex の Web 自動化適性から:

1. **本願書誌情報の J-PlatPat 自動取得** (90〜150 行) — `tools/recon.py` で wsp 系 API 偵察 → `modules/jplatpat_bibliography.py` 新設 → `case_service.create_case` で本願 PDF DL 直後に書誌情報も取得
2. **Step 4.5 候補リストの正規化動作 smoke 確認** — Playwright で Step 4.5 開いて hit chip カウントを HTML から確認
3. **(将来) cosmetic-info.jp 検索結果取り込み (Phase 2)** — 偵察 → API 直叩き

これらは本ドキュメント (LLM レイヤ) とは独立した作業候補。

---

## 改定履歴

- 2026-05-07 初版作成 (本セッション)
- 主原因仮説の訂正: 「GLM モデル名が架空」→「GLM は動いている」(ユーザ確認)
- 残候補: JSON パース失敗 / タイムアウト / 空応答 / 429 / citation merge 起因のプロンプト膨張
