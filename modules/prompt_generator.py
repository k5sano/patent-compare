#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
対比用プロンプト自動生成モジュール（最重要）

入力:
- segments: 請求項分節
- citations: 引用文献構造化テキスト（1件 or 複数件）
- keywords: キーワードグループ（任意）

出力: Claudeチャットに貼り付けるプロンプト文字列
"""

import json
import yaml
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()

def _load_prompt_config():
    """config.yaml からプロンプト生成設定を読み込む"""
    config_path = _PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            prompt_cfg = cfg.get("prompt", {})
            return prompt_cfg
        except Exception:
            pass
    return {}

_prompt_cfg = _load_prompt_config()

# テキスト量上限（全文献合計）
MAX_TOTAL_CHARS = _prompt_cfg.get("max_total_chars", 80000)

# セクション優先順位（テキスト量超過時のトリミング用）
SECTION_PRIORITY = _prompt_cfg.get("section_priority",
    ["実施例", "比較例", "請求項", "手段", "効果", "実施形態", "課題", "背景技術", "技術分野"])


def _build_task_definition():
    """タスク定義セクション (件数依存を排除して静的化 → prompt cache の対象)。"""
    return """## タスク
あなたは日本の特許審査における拒絶理由構成を支援する先行技術調査の専門家です。
以下の本願（出願中の特許）の請求項の構成要件と、引用文献（先行技術）を対比し、
各構成要件が引用文献に開示されているかを判定してください。
複数件の場合は文献ごとに独立して判定を行い、文献ごとにJSON結果を出力してください。"""


def _build_citation_priority_rules():
    """引用優先順位ルール"""
    return """## 引用箇所の優先順位
引用文献から該当記載を探す際は、以下の優先順位で引用してください：
1. **実施例**（具体的な配合例、実験データ、数値データ）— 最も証拠力が強い
2. **検出された表（配合表・比較例データ等）** — 本文とは別に冒頭の「### 【検出された表】」セクションにまとめて提示します。**表中の成分・配合量・物性値は最も信頼性の高い証拠**なので必ず検討対象に含めてください
3. **詳細な定義が書かれた箇所**（「本発明において○○とは」等の定義段落）
4. **請求項（クレーム）** — 権利範囲として明確
5. **一言でも言及がある箇所** — 最低限の開示

**重要**: 「### 【検出された表】」セクションの内容は段落本文と同じ出典（【XXXX】で段落番号を記載）ですが、実施例・比較例の数値表としてはここから引用することを推奨します。"""


def _build_cited_location_notation_rules():
    """cited_location セルの統一記法ルール (memory/reference_cited_ref_notation.md と同期)"""
    return """## cited_location の記法ルール（必ず厳守）
`cited_location` フィールドは以下の **コンパクト記法**で出力してください。検索報告書への自動転記のため、自然文ではなく次の形式に統一します。

### 接頭辞
| 種類 | 接頭辞 | 例 |
|---|---|---|
| 段落 | （なし、数字のみ） | `21` / `41-45` / `21,39` |
| 請求項 | `CL` | `CL1` / `CL1,5` / `CL1-3` |
| 図 | `F` | `F1` / `F1a` / `F5C` |
| 表 | `T` | `T4` |
| 化学構造（化N） | `K` | `K2` |
| 数式（式N） | `E` | `E3` |
| 数（数N） | `S` | `S1` |
| ページ | `P` | `P1A2-4` (1ページ左上欄2-4行) |
| カラム/行 | `C` / `G` | `C4G12-15` (4カラム12-15行) |

### 区切り
- 異なる種類は **`;`** で区切る: `20;F2;CL3;T4`
- 同種内の複数指定は **`,`** で: `21,39`
- 範囲は **`-`** で: `41-45` / `CL1-3` / `F1-3`

### コメント
- **`"...`**: その後を検索報告書の備考欄に転記するコメント（例: `20;"上位概念のみ`）
- **`//...`**: 防備録メモ（外部に転記しない）

### 出力例
- 段落20と図2と請求項3で開示: `20;F2;CL3`
- 段落21,39と段落41-45で開示: `21,39,41-45`
- 表4の段落15に記載: `T4;15`
- **表1と表2に記載**: `T1,2`     ← `T1;T2` ❌ 同種は ; ではなく , で連結
- **段落23と段落24に記載**: `23,24`  ← `0023;0024` ❌ ゼロパディング不要
- 段落20で記載されているが上位概念のみ: `20;/上位概念のみ`

**判定が ○ または △ の時は `cited_location` 必須 (空文字禁止)**:
- 引例本文が乏しくファミリー文献 (例「WO2019107497 の EP 対応」「同一発明の JP/US 対応」)
  を根拠にする場合でも、**本引例自体から** 最も近い記載 (請求項番号 `CL1`、段落番号
  `0023`、表番号 `T4` 等) を **必ず 1 つ以上引用** すること
- 開示が要約レベルしかなくても `CL1` など最低 1 件は入れる
- 「ファミリー参照のみで本引例には該当箇所なし」と判断するなら judgment は × にする
- 空文字 `""` を許すのは judgment が × の時 **のみ**
- **△ で「複合要件の一部のみ開示」と書いた場合**は、その「開示されている部分」の
  記載箇所を必ず cited_location に入れる (例: 本願「A+B+C+D」のうち A,B,C が記載
  なら `0023,0024;T1,2`)。「記載なし」と判定理由に書きながら cited_location が
  空 → 矛盾なので × か ○ に修正すること

**記法の禁止事項**:
- 「段落【0020】」「請求項3」のような自然文表記は使わない（パーサが認識しない）
- 「、」や「。」での連結は使わない（必ず `;` または `,`）
- **同種の要素を ; で繋がない**: `T1;T2;T3` ❌ → `T1,2,3` ✓
  / `CL1;CL2;CL3` ❌ → `CL1,2,3` ✓ / `F2;F5` ❌ → `F2,5` ✓
- **段落番号にゼロパディングしない**: `0023;0024` ❌ → `23,24` ✓
  / `00230;00450` ❌ → `230,450` ✓ (内部で 4 桁化される)
- 全角数字・全角英字も認識するがなるべく半角で
"""


def _build_judgment_criteria():
    """判定基準"""
    return """## 判定基準

各構成要件について以下の3段階で判定してください。
**上位概念/下位概念の方向に特に注意** (見逃し多発ポイント)。

### ○（一致／充足）
以下のいずれかなら ○:
- (a) 引用文献に同一又は実質的に同一の構成が明確に記載されている
- (b) **本願が上位概念、引用文献が下位概念（具体例）を開示** → ○
  - 例: 本願「ポリオール」 + 引例「グリセリン」「プロピレングリコール」「1,3-BG」 → **○**
  - 例: 本願「アニオン界面活性剤」 + 引例「ラウリル硫酸ナトリウム」「SLS」 → **○**
  - 例: 本願「アルコール」 + 引例「エタノール」「イソプロパノール」 → **○**
  - 例: 本願「油剤」 + 引例「スクワラン」「ホホバ油」 → **○**
  - 例: 本願「シリコーン樹脂」 + 引例「KF-9909」「X-25-9138A」(具体製品名) → **○**
  - 法理: 上位概念は下位概念を包含するため、下位概念の開示は上位概念の充足になる
- (c) 数値範囲が完全に包含されている
  - 例: 本願「1〜10質量%」 + 引例「5質量%」 → **○** (引例値が本願範囲内)

### △（部分一致／相違あり）
- (a) **本願が下位概念、引用文献が上位概念のみ** → △
  - 例: 本願「グリセリン」 + 引例「ポリオール」 → △ (上位概念のみで具体名なし)
- (b) 数値範囲が一部重複
  - 例: 本願「1〜10%」 + 引例「8〜15%」 → △ (重複は 8〜10% のみ)
- (c) 類似だが厳密には異なる構成
  - 例: 本願「カチオン性界面活性剤」 + 引例「両性界面活性剤」 → △
- (d) **複合要件 (A + B + C + …) のうち一部のみ開示** → △ (★ 重要)
  - 1 つの構成要件が複数の必須要素 (例「A、B、C、D の組成物」) を要求する場合、
    引例にそのうち **少なくとも 1 つでも明示記載** があれば △ (× ではない)
  - 全要素が記載されている場合のみ ○
  - **記載されている要素の cited_location は必ず明記する** (例: A は段落23、
    B は段落24、C は表1 → `23,24;T1`)。記載なしと書いて場所が無いのは矛盾
  - 例: 本願「(a)シリコーンポリアミド + (b)シリコーン樹脂 + (c)揮発性油 +
    (d)粉体 + (e)グリセロール化シリコーン樹脂 を含む組成物」+
    引例「(a)〜(d) は段落23,24と表1に記載、(e)グリセロール化シリコーン樹脂は無し」
    → **△** (× ではない、(a)〜(d) が記載されているため)
    judgment_reason: 「(a)シリコーンポリアミド・(b)シリコーン樹脂・(c)揮発性油・
    (d)粉体は記載あり、(e)グリセロール化シリコーン樹脂のみ記載なし。(明示記載)」
    cited_location: `23,24;T1` (記載部分の場所を明記)
  - 「記載されているが」「一部記載」「ほぼ記載」と書いたら必ず △ (× にしない)

### ×（不一致）
- 引用文献に対応する記載が見当たらない、または明確に異なる構成が記載されている
- **× を選ぶ前に必ず以下を実施**:
  1. **上位概念チェック**: 本願語の上位概念 (例: 本願「グリセリン」→ 上位「ポリオール」
     「多価アルコール」「保湿剤」) で引例を再走査
     - 上位概念**もない** → × (judgment_reason に「上位概念 (○○、△△) も記載なし」と明記)
     - 上位概念**だけある** → **△** に格上げ ((階層判定: 引例上位 ⊃ 本願下位))
  2. **複合要件チェック**: 構成要件が複数要素 (A+B+C+...) を含む場合、引例に
     **どれか 1 つでも記載があるか** を確認
     - 1 つでも記載あり → **△** に格上げ (上記 △ (d) ルール参照)
     - **全要素が記載なし** の時のみ ×
- judgment_reason 例:
  - 良い × 例: 「ポリオール、多価アルコール、グリセリン いずれも引例に記載なし。(明示記載)」
  - 良い △ 例: 「ポリオールの記載はあるがグリセリン等の具体名なし。
    (階層判定: 引例ポリオール ⊃ 本願グリセリン)」
  - 良い △ 例 (複合): 「(a)〜(d) は記載あり、(e)グリセロール化シリコーン樹脂のみ記載なし。」
  - 悪い × 例: 「グリセリンの記載なし。」← 上位概念チェック未実施なので不可
  - 悪い × 例 (複合): 「(a)〜(d) は記載されるが (e) の記載なし」と書いたまま × にする
    ← 部分開示があるので △ にすべき

## 上位概念判定の重要原則 (★ 見逃し防止)
- 引例が **具体的な物質名・商品名・化学式・成分名** を出していて、それが本願の用語に
  **業界一般常識で包含される** 場合は **必ず ○** にする (△ にしない)
- 「グリセリン ∈ ポリオール」「ステアリン酸 ∈ 高級脂肪酸」「PET ∈ ポリエステル」など
  化学・素材分野の階層関係は本知識で確実に判定すること
- 判断に迷ったときの基本姿勢: **「引例の具体例が本願の上位概念に含まれるなら ○」**

### ★★ 方向性は逆転禁止 (最頻ミス) ★★
| 本願請求項   | 引例の開示 | 判定 | 理由 |
|---|---|---|---|
| **上位**(ポリオール)    | **下位**(グリセリン)  | **○** | 引例の具体例が本願範囲に入る (新規性否定) |
| **下位**(グリセリン)    | **上位**(ポリオール)  | **△** | 引例は上位概念のみで具体名なし、本願下位の特定は無い |
| 上位(界面活性剤)   | 上位(界面活性剤)   | ○   | 同一概念 |
| 下位(SLS)         | 下位(SLS)         | ○   | 同一具体物 |

**禁止**: 本願「グリセリン」+ 引例「ポリオール」を ○ にしない (方向逆転)。
引例にグリセリン等の具体名がなければ本願下位は充足しない。

## ハルシネーション抑制ルール (★ LLM 推論を許可する代わりに必読)

上位/下位の階層判定など LLM の知識を使う判定は便利だが、**捏造リスク** がある。
以下を厳守:

### 1. 引例に書かれていない成分・構成を絶対に作らない
- 引例に出ていない成分名・配合実施例・組合せを捏造しない
- 「おそらく」「一般的に」「考えられる」での判定禁止
- cited_text には **引例本文から原文そのまま抜粋** (要約・言い換え禁止)
- 引用できないなら ×
- **例外 1 (外部参照)**: 引例に明示されている成分の **物性値** (HLB, 炭素数, 融点, 分子量等)
  を本願請求項の数値要件と照合する目的に限り、メーカーカタログ・PubChem 等
  外部公開情報で確認してよい (下記タイプ "外部参照" 参照)
- **例外 2 (計算)**: 引例の実施例・配合表に明示されている数値 (配合量・含有量等)
  から、本願請求項の比率・割合・数値範囲を計算で導出してよい (下記タイプ "計算" 参照)
  - 例: 引例「樹脂 A 4g、樹脂 B 5g」→ 比 0.8 と計算 → 本願「比 0.8 以上」と照合

### 2. 判定根拠の種類を judgment_reason の末尾に括弧書きで必ず明示
- **(明示記載)**: 引例に本願語と同一表記で記載されている
- **(階層判定: <本願上位> ⊃ <引例下位>)**: 上位⊃下位で包含
  - 例: `(階層判定: ポリオール ⊃ グリセリン)`
- **(同義語: <引例語> = <本願語>)**: 同義語/別名/INCI/商品名 + 機能的・記述的同義
  の対応 (完全に同じ意味なら ○)
  - 別名・表記揺れ:
    - 例: `(同義語: グリセリン = glycerin = グリセロール)`
    - 例: `(同義語: PET = ポリエチレンテレフタレート)`
    - 例: `(同義語: SLS = ラウリル硫酸ナトリウム)`
  - **機能的・記述的同義 (ユーザー指示 2026-05-05): 用語と説明文が完全に同義なら ○**
    - 例: 本願「無水」 + 引例「水を含まない」「水分含有量 0%」「実質的に水を含まない」
      → `(同義語: 水を含まない = 無水)` → **○**
    - 例: 本願「常温固体」 + 引例「室温で固体」「25℃で固体」
      → `(同義語: 室温で固体 = 常温固体)` → **○**
    - 例: 本願「揮発性」 + 引例「揮発する」「常圧で気化しやすい」
      → `(同義語: 揮発する = 揮発性)` → **○**
    - 注意: **完全に同義 (= 意味が一致) と確信できる場合のみ**。曖昧なら ×
- **(計算: <算式と引用元>)**: 本願請求項に **数値範囲・比率・割合** 制限があり、
  引例の **実施例 / 配合表 / 表 N** に具体的な配合量・含有量・組成比が記載されていて、
  そこから **計算で本願範囲を充足しているか判定** できる場合
  - 用途: 質量比 / モル比 / 含有率 / 数量 / 厚みの計算判定
  - 例: 本願「樹脂 A : 樹脂 B = 0.8 以上」+ 引例「実施例3: A 4g, B 5g」
    → 比 = 4/5 = 0.8 → **○** (計算: 表1 実施例3 A=4g/B=5g → A/B=0.8、本願「0.8 以上」充足)
  - 例: 本願「成分 X を 0.1〜5 質量%」+ 引例「実施例 1: X 2g, 全量 100g」
    → 2% → **○** (計算: 段落0034 実施例1 X 2g/全量 100g = 2%、本願範囲 0.1〜5% 内)
  - **計算結果は必ず本願範囲と照合**: 範囲内なら ○、外なら ×、境界曖昧なら △
  - **計算前提**: 配合量の単位 (g/mol/%) と全体量 (or 比較対象) が引例に明示されていること
  - **しないこと**: 配合量が「適量」「微量」など定性表現なら ×。引例に数値が無いなら計算しない (捏造禁止)
- **(外部参照: <根拠タイプ>: <値> @ <URL>)**: 引例の成分名と本願の数値要件
  との一致を、メーカーカタログ等の外部公開情報で確認した場合
  - 用途: 物性値 (HLB, 融点, 分子量, ガラス転移温度, 屈折率 等) や
    商品の組成・規格が引例本文に明記されていないが、本願請求項の数値範囲
    に該当することを公的データで確認できる時
  - 例: 本願「HLB 9.5 以上のノニオン界面活性剤」+ 引例「ポリソルベート20」
    → `(外部参照: HLB=16.7 @ https://www.croda.com/.../tween-20-tds.pdf)`
  - 例: 本願「炭素数 12 以上の脂肪酸」+ 引例「ステアリン酸」
    → `(外部参照: ステアリン酸=C18 @ https://pubchem.ncbi.nlm.nih.gov/compound/5281)`
  - **必須**: 実際に WebSearch / WebFetch ツールで確認した URL を貼る (記憶からの数値捏造禁止)
  - 不確実なら × にする (推測値で ○ にしない)
- 上記いずれも該当しないなら、その判定は **× にする** (推測根拠は使わない)

### 3. 「迷ったら △」を禁止 — ×/△ の境目を厳密に
- 引例本文に該当語の明示記載なし + 階層・同義語関係も成立しない → **×**
- △ を選ぶには「上位/下位の方向性」「数値範囲の部分重複」「類似 (但し厳密に異なる) 構成」
  のいずれか具体的根拠が必要
- 「なんとなく似ている」「関連がありそう」だけでは × を選ぶ

### 4. 回答前のセルフチェック (各構成要件で実行)
- ✅ judgment_reason に書いた根拠は引例本文に実在するか? cited_text に抜粋できるか?
- ✅ 階層判定なら「○○ ⊃ △△」は化学・素材分野の一般常識として確実か?
  (例: ポリオール ⊃ グリセリン ✓、油剤 ⊃ シリコーン樹脂 ✗ ← シリコーン樹脂は油剤とは別カテゴリ)
- ✅ 外部参照型なら **取得した URL を貼ったか** (記憶ベースの数値捏造禁止)
- ✅ **本願請求項に数値範囲・比率があるか確認**。あれば、引例の実施例・配合表から
  計算可能か検討し、計算で充足判定できるなら ○ にする (「明示なし」で △ で止めない)
- ✅ **× を選ぶ前に上位概念チェック**: 本願語の上位概念 (1〜2 段階上) で引例を
  再走査し、上位概念があれば △ に格上げ。上位も無いことを judgment_reason に
  明記する (「上位概念 (○○) も記載なし」)
- ✅ 確信が持てないなら判定を 1 段下げる (○ → △、△ → ×)

### 5. 数値範囲判定の最重要ルール (★ 「請求項範囲に入る実施例があれば充足」)
- 本願請求項に **複数の数値範囲** が書かれている場合 (例: 「80 質量%以下、好ましくは
  40〜70 質量%」) は、**claim 本体の最も広い範囲で判定する**。
- 「好ましくは / より好ましくは / 特に好ましくは / 好適には / より詳細には」 は
  **任意の補助情報** であり 判定の基準ではない。引例がこれを外れていても **△ にしない**。
- **引例の実施例・配合表に具体的な数値があり、それが claim 本体範囲内なら必ず ○**。

  | 例 | 本願請求項 | 引例実施例 | 判定 |
  |---|---|---|---|
  | 1 | 「80 質量%以下、好ましくは 40〜70 質量%」 | イソデカン 30% | **○** (80% 以下を充足。好適範囲外でも本体範囲内) |
  | 2 | 「樹脂A : 樹脂B = 0.8 以上」 | A 4g、B 5g (= 0.8) | **○** (計算で 0.8、claim 範囲内) |
  | 3 | 「0.1〜5 質量%」 | 成分X 2g/全量 100g (= 2%) | **○** (本体範囲 0.1〜5% 内) |
  | 4 | 「80 質量%以下」 | 配合 90% | **×** (本体範囲外) |
  | 5 | 「50% 以下、好ましくは 25〜40%、より詳細には 3〜15%」 | 着色剤 8% | **○** (50% 以下を充足。詳細範囲 3〜15% にも該当だが本体で十分) |

- **セルフ確認 (必ず実行)**:
  - 引例値を本願 claim 本体範囲 (最も広い範囲) と照合したか?
  - 好適範囲だけと比較していないか?

### 6. 従属請求項 (sub_claims) は「追加された限定」のみを判定する (★)
- 従属請求項 (請求項 2 以降) は通常「請求項 N の○○であって、さらに △△ である」という
  形式。判定対象は **追加された△△部分のみ** (請求項 1 の構成は別途判定済なので重複評価しない)
- judgment_reason は **追加限定にフォーカスして** 書く
  - 例: 請求項3 「請求項1 の組成物であって、グリセロール化シリコーン樹脂の質量比が 0.8 以上」
    → 判定対象: **「質量比 0.8 以上」のみ**
    → 良い reason: 「実施例3 の表1 で質量比 0.8 が示される。(計算: A=4g/B=5g → 0.8、本願範囲内)」
    → 悪い reason: 「組成物としての記載はあるが質量比は不明」← 組成物部分は冗長
- requirement_text には **追加された限定の本文** をそのまま (or 短縮して) 入れる
  (請求項全文を入れない、追加部分が分かるように)
- 上位概念 / 計算 / 外部参照 / 同義語の各タイプも従属請求項で適用される

### 7. 「不明確」「比較困難」「確認可能だが」等の曖昧表現で △ にするのは禁止
- 「実施例での配合量が確認可能だが本願範囲との完全対比は不明確」のような **判断回避**
  を judgment_reason に書かない
- **数値が引例にあるなら必ず取り出して照合せよ**:
  1. 引例本文 (実施例・配合表) から該当成分の数値を **抜き出す** (cited_text に貼る)
  2. 必要なら計算する (タイプ "(計算)")
  3. 本願 claim **本体範囲** (最広範囲、好適/より好適/より詳細を除く) と照合
  4. 範囲内 → ○、範囲外 → ×、引例数値と本願範囲の単位/換算が不明 → ×
- 「対比困難」「不明確」「確認可能だが…」などの曖昧表現を使わない
- 数値があるのに △ で止めるのは禁止 (○ か × かを必ず出す)

### 5. 利用可能ツール (Claude Code CLI 経由)
- **WebSearch / WebFetch** が使えます。引例の成分名から物性値・組成・規格を
  外部で確認したいときに限り使用。取得した URL を judgment_reason に必ず貼る
- 一般的常識・記憶ベースで数値を答えるのは禁止 (誤値リスク高)
- 検索失敗・確証なしなら × にする

## judgment_reason の書き方
- 全判定共通: **末尾に根拠タイプを括弧書き** (上記ハルシネーション抑制ルール 2 参照)
- **○ の場合**: 一致根拠を簡潔に + タイプ
  * 例: 「同一構成が明示されている。(明示記載)」
  * 例: 「グリセリンは本願ポリオールに包含される。(階層判定: ポリオール ⊃ グリセリン)」
- **△ / × の場合**: **相違点を 1 文で簡潔に**完結 (句点「。」必須) + タイプ
  * 良い例: 「(C)成分はカチオン性架橋ビニル共重合体であり、天然多糖系の本願成分(A)と相違する。(明示記載)」
  * 悪い例: 「必須成分は(A)エーテル硫酸塩、(B)エーテルカルボン酸塩、(C)カチオン性架橋…」（途中で止まっている）
  * 文字数の目安: 30〜80 文字 (タイプ括弧含めて 100 字以内目安)
- 改行や箇条書きは含めない（貼付用のため 1 行に収まること）。"""


def _build_field_notes(field):
    """分野特有の注意事項"""
    if field == "cosmetics":
        return """## 化粧品分野の注意事項
- **成分名の表記ゆれに注意**: INCI名、和名、商品名、化学名が混在します
  - 例: 「BG」=「1,3-ブチレングリコール」=「1,3-Butylene Glycol」
  - 例: 「ペリセア」=「ジラウロイルグルタミン酸リシンNa」
- **配合量**: 成分だけでなく配合量（質量%等）の範囲も対比してください
- **配合理由**: 成分がどのような目的で配合されているかも重要です
  （例: 「保湿剤として」「乳化剤として」「防腐剤として」）
- **上位概念・下位概念の関係**:
  - 引用文献が下位概念（具体名）を開示 → 本願の上位概念に対して○
  - 引用文献が上位概念のみ → 本願の下位概念に対して△
- **実施例の配合表**: 表中の配合成分と配合量は最も信頼性の高い証拠です"""
    elif field == "laminate":
        return """## 積層体分野の注意事項
- **層構成の対応関係**: 層の数、順序、各層の材料を対比してください
- **材料名の同義語に注意**:
  - 例: 「PET」=「ポリエチレンテレフタレート」
  - 例: 「ナイロン」=「ポリアミド」=「PA」
- **厚さ・物性値**: 数値範囲の重複を確認してください
- **製法の限定**: 「二軸延伸」「蒸着」等の製法限定も構成要件です"""
    return ""


def _build_segments_section(segments):
    """本願の請求項分節セクション"""
    lines = ["## 本願の請求項 構成要件"]
    for claim in segments:
        claim_num = claim["claim_number"]
        dep_type = "独立" if claim["is_independent"] else f"従属（→請求項{','.join(map(str, claim['dependencies']))}）"
        lines.append(f"\n### 請求項{claim_num}（{dep_type}）")
        for seg in claim["segments"]:
            lines.append(f"- **{seg['id']}**: {seg['text']}")
    return "\n".join(lines)


# compact 時に必ず残す本願セクション (実施例の具体例・配合表は判定の根拠になる)
_COMPACT_KEEP_SECTIONS = {"実施例", "実施形態", "比較例", "課題", "表"}


def _build_hongan_body_section(hongan, *, compact=False, related_para_ids=None,
                                compact_mode="relaxed"):
    """本願の明細書本文 + 表セクション (実施例の具体例を LLM に提示)。

    Parameters:
        hongan: hongan.json
        compact: True にすると本願段落を絞り込む
        related_para_ids: 関連段落の id セット (related_paragraphs.json を分節横断で
                         マージしたもの)
        compact_mode:
            "relaxed" (default): 関連段落 ∪ 重要セクション (実施例/実施形態/比較例/課題)
            "strict": 関連段落のみ (＋表)。実施例の数値証拠が失われる可能性あり
    """
    if not hongan:
        return ""

    paragraphs = hongan.get("paragraphs") or []
    if compact and paragraphs:
        keep_ids = set(related_para_ids or [])
        if compact_mode == "strict":
            filtered = [p for p in paragraphs if str(p.get("id", "")) in keep_ids]
            mode_label = "strict: 関連段落のみ"
        else:  # relaxed
            filtered = []
            for p in paragraphs:
                pid = str(p.get("id", ""))
                section = p.get("section", "") or ""
                if pid in keep_ids:
                    filtered.append(p)
                elif any(k in section for k in _COMPACT_KEEP_SECTIONS):
                    filtered.append(p)
            mode_label = "relaxed: 関連段落 + 実施例/実施形態/比較例/課題セクション"
        title = (
            f"## 本願の明細書本文 (compact {mode_label}, 計 {len(filtered)} / {len(paragraphs)} 段落)"
        )
        out_paragraphs = filtered
    else:
        title = "## 本願の明細書本文 (全段落) — 実施例の具体例参照用"
        out_paragraphs = paragraphs

    lines = [title]
    for p in out_paragraphs:
        lines.append(f"【{p.get('id', '')}】({p.get('section', '')}) {p.get('text', '')}")

    tables = hongan.get("tables") or []
    if tables:
        lines.append("")
        lines.append(f"### 本願の表 (全 {len(tables)} 件)")
        for i, t in enumerate(tables):
            tbl_label = t.get("caption") or t.get("title") or f"表 {i+1}"
            lines.append(f"#### {tbl_label}")
            rows = t.get("rows") or t.get("data") or []
            if rows:
                for row in rows:
                    if isinstance(row, list):
                        lines.append("\t".join(str(x) for x in row))
                    else:
                        lines.append(str(row))
            else:
                content = t.get("content")
                if content:
                    lines.append(content)
    return "\n".join(lines) if len(lines) > 1 else ""


def _build_keywords_section(keywords):
    """キーワードグループセクション"""
    if not keywords:
        return ""

    lines = ["## キーワードグループ（参照用）"]
    lines.append("以下は本願の構成要件に対応するキーワードグループです。表記ゆれの参考にしてください。")
    for group in keywords:
        lines.append(f"\n### グループ{group['group_id']}: {group['label']}（分節: {', '.join(group['segment_ids'])}）")
        for kw in group["keywords"]:
            lines.append(f"- {kw['term']}（{kw['type']}、出典: {kw['source']}）")
    return "\n".join(lines)


def _trim_citation_text(citation, max_chars):
    """引用文献テキストをセクション優先順位に基づいてトリミング。

    順序:
      1. 請求の範囲（claims）
      2. 検出された表（tables）— 実施例の配合表は最重要証拠なので常に優先
      3. セクション優先順位に基づく段落（SECTION_PRIORITY）
      4. 残りの段落
    表として既に含めた段落は 3/4 で重複させない。
    """
    paragraphs = citation.get("paragraphs", [])
    claims = citation.get("claims", [])
    tables = citation.get("tables", []) or []

    by_section = {}
    for para in paragraphs:
        section = para.get("section", "その他")
        by_section.setdefault(section, []).append(para)

    selected = []
    total_chars = 0

    claims_text = ""
    if claims:
        claims_lines = ["### 【特許請求の範囲】"]
        for cl in claims:
            claims_lines.append(f"【請求項{cl['number']}】{cl['text']}")
        claims_text = "\n".join(claims_lines)
        total_chars += len(claims_text)

    # 表（実施例の配合表など）を最優先で含める
    tables_lines = []
    table_para_ids = set()
    if tables:
        tables_lines.append("### 【検出された表（実施例の配合表・比較例データ等）】")
        for t in tables:
            pid = str(t.get("paragraph_id", "")).strip()
            tid = t.get("id", "表?")
            page = t.get("page", "?")
            section = t.get("section", "")
            content = t.get("content", "") or ""
            if not content:
                continue
            header = f"#### {tid}（段落【{pid}】 p.{page}"
            if section:
                header += f" / {section}"
            header += "）"
            entry = f"{header}\n{content}"
            if total_chars + len(entry) > max_chars:
                # 予算オーバーでも最低 1 つは入れる（claims を削ってでも）
                if not table_para_ids:
                    tables_lines.append(entry)
                    total_chars += len(entry)
                    if pid:
                        table_para_ids.add(pid)
                break
            tables_lines.append(entry)
            total_chars += len(entry)
            if pid:
                table_para_ids.add(pid)
        if len(tables_lines) == 1:
            # ヘッダだけ残ったらリセット
            tables_lines = []

    for section_name in SECTION_PRIORITY:
        if section_name == "請求項":
            continue
        paras = by_section.get(section_name, [])
        for para in paras:
            if para["id"] in table_para_ids:
                continue  # 表で既に含めた段落はスキップ
            para_text = f"【{para['id']}】{para['text']}"
            if total_chars + len(para_text) > max_chars:
                break
            selected.append(para)
            total_chars += len(para_text)

    included_ids = {p["id"] for p in selected} | table_para_ids
    for para in paragraphs:
        if para["id"] not in included_ids:
            para_text = f"【{para['id']}】{para['text']}"
            if total_chars + len(para_text) > max_chars:
                break
            selected.append(para)
            total_chars += len(para_text)

    selected.sort(key=lambda p: p["id"])

    lines = []
    if claims_text:
        lines.append(claims_text)
    if tables_lines:
        lines.append("\n".join(tables_lines))
    current_section = None
    for para in selected:
        section = para.get("section", "")
        if section != current_section:
            lines.append(f"\n### 【{section}】")
            current_section = section
        lines.append(f"【{para['id']}】{para['text']}")

    return "\n".join(lines)


def _build_citations_section(citations):
    """複数引用文献テキストセクション"""
    num = len(citations)
    # 文献あたりの文字数上限を均等配分
    per_citation_chars = MAX_TOTAL_CHARS // max(num, 1)

    sections = []
    for i, citation in enumerate(citations, 1):
        doc_id = citation.get("patent_number", citation.get("file_name", "不明"))
        role = citation.get("role", "主引例")
        label = citation.get("label", doc_id)

        lines = [f"## 引用文献{i}: {label}（{doc_id}）"]
        lines.append(f"役割: {role}")
        lines.append("")
        full_text = _trim_citation_text(citation, per_citation_chars)
        lines.append(full_text)
        sections.append("\n".join(lines))

    return "\n\n---\n\n".join(sections)


def _build_output_format_multi(citations, segments):
    """複数文献対応の出力フォーマット指定"""
    # 請求項1の分節IDリスト
    claim1_ids = []
    sub_claims = []
    for claim in segments:
        if claim["claim_number"] == 1:
            for seg in claim["segments"]:
                claim1_ids.append(seg["id"])
        else:
            sub_claims.append(claim)

    # 文献情報リスト
    doc_list = []
    for cit in citations:
        doc_id = cit.get("patent_number", cit.get("file_name", "不明"))
        role = cit.get("role", "主引例")
        doc_list.append({"id": doc_id, "role": role})

    # 比較結果のサンプル（最初の分節2つ分）
    example_comparisons = []
    for seg_id in claim1_ids[:2]:
        example_comparisons.append(f"""        {{
            "requirement_id": "{seg_id}",
            "judgment": "○ or △ or ×",
            "judgment_reason": "判定理由を具体的に記載",
            "cited_location": "コンパクト記法 例: 20;F2;CL3 (段落20+図2+請求項3) / 21,39,41-45 (段落の複数+範囲) / 20;\\\"備考",
            "section_type": "実施例 or 定義 or クレーム or 言及",
            "cited_text": "引用文献の該当記載をそのまま抜粋",
            "formulation_reason": "配合理由があれば記載（化粧品分野）",
            "note": "補足があれば"
        }}""")
    comparisons_str = ",\n".join(example_comparisons)

    sub_claim_example = ""
    if sub_claims:
        sub_claim_example = f""",
    "sub_claims": [
        {{
            "claim_number": {sub_claims[0]['claim_number']},
            "requirement_text": "★ 追加された限定事項のみ (請求項1部分は除く)。例: 「グリセロール化シリコーン樹脂の質量比が 0.8 以上」",
            "judgment": "○ or △ or ×",
            "judgment_reason": "★ 追加限定にフォーカス。請求項1部分は冗長判定しない。例: 「実施例3 表1 で質量比 0.8 が示される (計算: A=4g/B=5g → 0.8、本願範囲内)」",
            "cited_location": "コンパクト記法 (例: 20 / CL3 / T4;15 等)",
            "note": ""
        }}
    ]"""

    # 単一文献の場合
    if len(citations) == 1:
        doc_id = doc_list[0]["id"]
        role = doc_list[0]["role"]
        return f"""## 出力フォーマット
以下のJSON形式で回答してください。必ず全ての構成要件（{', '.join(claim1_ids)}）について判定を含めてください。

```json
{{
    "document_id": "{doc_id}",
    "document_role": "{role}",
    "comparisons": [
{comparisons_str},
        ... （全ての構成要件 {', '.join(claim1_ids)} について記載）
    ]{sub_claim_example},
    "overall_summary": "この引用文献の概要と本願との関連性を3-5文で記述",
    "category_suggestion": "X or Y or A（X=単独で拒絶可能, Y=組合せで拒絶可能, A=参考文献）",
    "rejection_relevance": "拒絶理由との関連性（例: 【進歩性欠如の主引例候補】）"
}}
```

### 出力時の注意
- judgment は必ず「○」「△」「×」のいずれかを使用してください
- cited_location は **コンパクト記法**（数字=段落、CL=請求項、F=図、T=表、K=化、E=式、S=数、; で連結、, で複数、- で範囲）で出力してください。自然文（「段落【0020】」等）は禁止
- cited_text は引用文献の記載をそのまま抜粋してください（要約ではなく原文）
- ×（不一致）の場合でも judgment_reason に「該当する記載なし」等の理由を記載してください"""

    # 複数文献の場合: results 配列でラップ
    doc_examples = []
    for d in doc_list[:2]:  # サンプルは2件まで
        doc_examples.append(f"""    {{
        "document_id": "{d['id']}",
        "document_role": "{d['role']}",
        "comparisons": [
{comparisons_str},
            ... （全構成要件 {', '.join(claim1_ids)} について）
        ]{sub_claim_example},
        "overall_summary": "文献の概要",
        "category_suggestion": "X or Y or A",
        "rejection_relevance": "拒絶理由との関連性"
    }}""")
    doc_examples_str = ",\n".join(doc_examples)

    remaining = ""
    if len(doc_list) > 2:
        remaining = f"\n        ... （残り{len(doc_list) - 2}件も同じ形式で）"

    return f"""## 出力フォーマット
**{len(citations)}件の文献それぞれについて**、以下のJSON形式で回答してください。
全文献の結果を `results` 配列にまとめてください。
必ず全ての構成要件（{', '.join(claim1_ids)}）について各文献ごとに判定を含めてください。

```json
{{
    "results": [
{doc_examples_str}{remaining}
    ]
}}
```

### 出力時の注意
- **文献ごとに独立して判定**してください（文献間の組合せ判断は不要）
- judgment は必ず「○」「△」「×」のいずれかを使用してください
- cited_location は **コンパクト記法**（数字=段落、CL=請求項、F=図、T=表、K=化、E=式、S=数、; で連結、, で複数、- で範囲）で出力してください。自然文（「段落【0020】」等）は禁止
- cited_text は引用文献の記載をそのまま抜粋してください（要約ではなく原文）
- ×（不一致）の場合でも judgment_reason に「該当する記載なし」等の理由を記載してください
- **全{len(citations)}件の文献について必ず結果を含めてください**
- **document_id は上記「## 引用文献N: ラベル（{', '.join(d['id'] for d in doc_list)}）」内のカッコ内の文字列をそのまま使ってください**（半角/全角・空白・ハイフン・スラッシュを変えずに）。表記揺れがあると UI で取り込めません"""


def generate_prompt(segments, citations, keywords=None, field="cosmetics", hongan=None,
                    *, compact_hongan=False, related_paragraphs=None,
                    compact_mode="relaxed"):
    """対比プロンプトを生成するメインエントリポイント

    Parameters:
        segments: 請求項分節データ (segments.json)
        citations: 引用文献データ。dict(1件) or list[dict](複数件)
        keywords: キーワードグループ (keywords.json)、任意
        field: "cosmetics" | "laminate"
        hongan: 本願データ (hongan.json) 任意。
        compact_hongan: True の場合、本願段落を「関連段落 + 重要セクション」に絞る。
                        related_paragraphs と組み合わせて使う。
        related_paragraphs: related_paragraphs.json (dict: seg_id → list[{id,page,...}])。
                           compact_hongan=True のときに参照される。

    Returns:
        プロンプト文字列
    """
    # 後方互換: dict1件の場合はリストに変換
    if isinstance(citations, dict):
        citations = [citations]

    num = len(citations)

    # フィールドを引用文献メタから取得（あれば）
    if citations and hasattr(citations[0], 'get'):
        field = citations[0].get("field", field)

    # compact mode の関連段落 ID 集合をマージ
    related_para_ids = set()
    if compact_hongan and related_paragraphs:
        for seg_id, paras in related_paragraphs.items():
            for p in (paras or []):
                pid = p.get("id") if isinstance(p, dict) else p
                if pid:
                    related_para_ids.add(str(pid))

    # 静的部分 (案件・実行ごとに変わらない) を先頭、動的部分 (案件固有) を末尾に
    # → Anthropic の prompt cache が先頭から最大 cache 境界までヒット
    # → 同一 field の連続対比で 2 回目以降のレイテンシ・トークンコスト削減
    sections = [
        # === 静的部分 (cache 対象) ===
        _build_task_definition(),
        _build_citation_priority_rules(),
        _build_cited_location_notation_rules(),
        _build_judgment_criteria(),
        _build_field_notes(field),

        # === 動的部分 (案件固有、毎回変わる) ===
        _build_segments_section(segments),
        _build_hongan_body_section(
            hongan, compact=compact_hongan, related_para_ids=related_para_ids,
            compact_mode=compact_mode,
        ),
        _build_keywords_section(keywords),
        _build_citations_section(citations),
        _build_output_format_multi(citations, segments),
    ]

    prompt = "\n\n---\n\n".join(s for s in sections if s.strip())
    return prompt

# ====================================================================
# 構成要件主体型 prompt (審査官実務に近い形式) — Phase: 試作
# ====================================================================
#
# 設計思想 (ユーザー実務指示 2026-05-05):
#   - 請求項を構成要件単位で分解し、文言ごとに引例本文と照合する
#   - 本願明細書は「言葉の定義・該当成分名・実施例の数値」を参酌する
#     ための辞書として扱う (全文を流し込まない)
#   - キーワードでマッチした段落・表のみを構成要件ごとに抜粋して prompt 化
#   - 構成要件単位のブロックを並べることで、判定漏れを抑制 +
#     「複合要件の一部開示→△」が自然に出やすい構造
#
# 利用方法:
#   from modules.prompt_generator import generate_prompt_requirement_first
#   prompt = generate_prompt_requirement_first(
#       segments, citations, keywords, field, hongan=hongan
#   )
#   raw = call_claude(prompt, model="sonnet")
#   # parse_response は既存と同じ JSON 出力形式

# 抜粋上限 (これ以上は段落が多くても切る)
_MAX_HONGAN_HITS_PER_SEG = 8
_MAX_CITATION_HITS_PER_SEG = 8
_MAX_PARA_TEXT_CHARS = 600  # 1段落の最大表示文字数 (長文段落は冒頭のみ)


def _build_segment_keyword_map(keywords):
    """seg_id → [term, ...] のマップを構築。

    keywords.json の構造: [{group_id, label, segment_ids:[...], keywords:[{term,type,source,...}]}, ...]
    """
    m = {}
    for group in (keywords or []):
        terms = [kw.get("term", "") for kw in group.get("keywords", []) if kw.get("term")]
        for seg_id in group.get("segment_ids", []):
            m.setdefault(seg_id, []).extend(terms)
    # 重複除去 (順序維持)
    for k in list(m.keys()):
        seen = set(); uniq = []
        for t in m[k]:
            if t not in seen:
                seen.add(t); uniq.append(t)
        m[k] = uniq
    return m


def _truncate(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def _find_matching_paragraphs(units, terms, max_hits):
    """段落リストからキーワードを含むものを返す (大文字小文字無視)。

    units: [{"id":..., "text":..., "section":..., "page":...}, ...]
    """
    if not terms or not units:
        return []
    norm_terms = [t for t in terms if t]
    if not norm_terms:
        return []
    out = []
    for u in units:
        text = u.get("text", "") or ""
        if any(t in text for t in norm_terms):
            out.append(u)
            if len(out) >= max_hits:
                break
    return out


def _find_matching_claims(claims, terms, max_hits=3):
    if not terms or not claims:
        return []
    out = []
    for cl in claims:
        text = cl.get("text", "") or ""
        if any(t in text for t in terms if t):
            out.append(cl)
            if len(out) >= max_hits:
                break
    return out


def _find_matching_tables(tables, terms, max_hits=3):
    """表本文 (rows をフラット化) にキーワードがあれば含める"""
    if not terms or not tables:
        return []
    out = []
    for t in tables:
        rows = t.get("rows") or t.get("data") or []
        flat = []
        if isinstance(rows, list):
            for r in rows:
                flat.append("\t".join(str(x) for x in r) if isinstance(r, list) else str(r))
        flat_text = "\n".join(flat) + "\n" + (t.get("content") or "") + (t.get("caption") or "")
        if any(term in flat_text for term in terms if term):
            out.append(t)
            if len(out) >= max_hits:
                break
    return out


def _build_citations_overview(citations):
    """各引例の概要 (請求項 + 主要メタ) のみ。本文は構成要件ブロックで抜粋する"""
    lines = ["## 引用文献の概要"]
    for i, cit in enumerate(citations, 1):
        label = cit.get("label") or cit.get("patent_number") or f"引例{i}"
        pn = cit.get("patent_number", "")
        lines.append(f"\n### 引用文献{i}: {label} ({pn})")
        title = cit.get("patent_title") or cit.get("title")
        if title:
            lines.append(f"- 発明の名称: {title}")
        claims = cit.get("claims", [])
        if claims:
            lines.append("- 請求項一覧:")
            for cl in claims[:5]:  # 多すぎなら先頭5件
                lines.append(f"  【請求項{cl.get('number','?')}】{_truncate(cl.get('text',''), 300)}")
            if len(claims) > 5:
                lines.append(f"  （…他 {len(claims)-5} 請求項）")
        # 課題 (進歩性チェックの素地として一行)
        for p in cit.get("paragraphs", []):
            sec = p.get("section", "") or ""
            if "課題" in sec:
                lines.append(f"- 引例の課題 (段落{p.get('id','')}): {_truncate(p.get('text',''), 250)}")
                break
    return "\n".join(lines)


def _build_requirement_blocks(segments, citations, hongan, seg_keywords):
    """構成要件単位のブロック群 — 請求項主体型 prompt の中核"""
    lines = ["## 構成要件別 対比 (各構成要件を独立に判定すること)"]

    hongan_paras = (hongan or {}).get("paragraphs", []) or []
    hongan_tables = (hongan or {}).get("tables", []) or []

    for claim in segments:
        claim_num = claim.get("claim_number", "?")
        is_indep = claim.get("is_independent", False)
        deps = claim.get("dependencies", [])
        dep_label = "独立" if is_indep else f"従属(→請求項{','.join(map(str, deps))})"
        lines.append(f"\n### 請求項{claim_num} ({dep_label})")

        for seg in claim.get("segments", []):
            seg_id = seg.get("id", "?")
            seg_text = seg.get("text", "")
            terms = seg_keywords.get(seg_id, [])
            terms_label = (
                "、".join(terms[:6]) + (f"…(他{len(terms)-6}件)" if len(terms) > 6 else "")
                if terms else "(キーワード未登録)"
            )

            lines.append(f"\n#### 構成要件 {seg_id}")
            lines.append(f"**請求項文言**: 「{seg_text}」")
            lines.append(f"**参酌キーワード**: {terms_label}")

            # 本願参酌
            h_para_hits = _find_matching_paragraphs(hongan_paras, terms, _MAX_HONGAN_HITS_PER_SEG)
            h_table_hits = _find_matching_tables(hongan_tables, terms)
            if h_para_hits or h_table_hits:
                lines.append("\n**本願参酌** (本願での定義・成分名・実施例の用法):")
                for p in h_para_hits:
                    lines.append(
                        f"- 段落{p.get('id','')}({p.get('section','')}): "
                        f"{_truncate(p.get('text',''), _MAX_PARA_TEXT_CHARS)}"
                    )
                for t in h_table_hits:
                    cap = t.get("caption") or t.get("title") or "(表)"
                    lines.append(f"- 本願表「{cap}」 ※詳細は末尾の表セクション参照")
            elif terms:
                lines.append("\n**本願参酌**: 本願段落でキーワードヒットなし (定義は明細書の他箇所か業界用語)")

            # 引例該当
            for i, cit in enumerate(citations, 1):
                cit_label = cit.get("label") or cit.get("patent_number") or f"引例{i}"
                cit_paras = cit.get("paragraphs", []) or []
                cit_claims = cit.get("claims", []) or []
                cit_tables = cit.get("tables", []) or []

                claim_hits = _find_matching_claims(cit_claims, terms)
                para_hits = _find_matching_paragraphs(cit_paras, terms, _MAX_CITATION_HITS_PER_SEG)
                table_hits = _find_matching_tables(cit_tables, terms)

                if claim_hits or para_hits or table_hits:
                    lines.append(f"\n**引例{i} ({cit_label}) 該当**:")
                    for cl in claim_hits:
                        lines.append(
                            f"- 請求項{cl.get('number','?')}: "
                            f"{_truncate(cl.get('text',''), 350)}"
                        )
                    for p in para_hits:
                        lines.append(
                            f"- 段落{p.get('id','')}({p.get('section','')}): "
                            f"{_truncate(p.get('text',''), _MAX_PARA_TEXT_CHARS)}"
                        )
                    for t in table_hits:
                        cap = t.get("caption") or t.get("title") or "(表)"
                        para_id = t.get("paragraph_id", "?")
                        lines.append(f"- 表「{cap}」 段落{para_id}付近 ※詳細は引例本文セクション参照")
                else:
                    lines.append(
                        f"\n**引例{i} ({cit_label})**: キーワードヒットなし → "
                        "上位概念チェック (本願語の上位概念で再走査)・同義語チェックを実施し、"
                        "それでも無ければ × (judgment_reason に「上位概念 (○○) も記載なし」と明記)"
                    )

    return "\n".join(lines)


def generate_prompt_requirement_first(segments, citations, keywords=None,
                                       field="cosmetics", hongan=None):
    """構成要件主体型の対比 prompt を生成 (新形式・試作版)。

    審査官の実務に沿って、請求項の構成要件ごとに「文言 + 本願参酌 + 引例該当」を
    並べて Claude に判定させる。本願明細書全文は流し込まない (キーワード経由で
    必要箇所のみ抜粋)。

    出力 JSON 形式は既存 generate_prompt と同じ (parse_response がそのまま使える)。

    Parameters と Returns は generate_prompt と同じ。
    """
    if isinstance(citations, dict):
        citations = [citations]
    if citations and hasattr(citations[0], 'get'):
        field = citations[0].get("field", field)

    seg_keywords = _build_segment_keyword_map(keywords)

    sections = [
        # === 静的部分 (cache 対象) ===
        _build_task_definition(),
        _build_citation_priority_rules(),
        _build_cited_location_notation_rules(),
        _build_judgment_criteria(),
        _build_field_notes(field),
        # === 動的部分 ===
        _build_citations_overview(citations),
        _build_requirement_blocks(segments, citations, hongan, seg_keywords),
        _build_output_format_multi(citations, segments),
    ]
    return "\n\n---\n\n".join(s for s in sections if s.strip())

