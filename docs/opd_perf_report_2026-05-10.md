# OPD Dossier Performance Report - 2026-05-10

## Scope

Feature Task A-0 として、OPD dossier 周りの主要ワークフローに処理時間計測を追加した。

対象:

- OPD書類収集
- 本願PDF内ISR OCR
- OPD添付PDF保存/OCR
- OPD添付PDF手動取込
- 拒絶理由・見解の翻訳/要約

## Recorded File

案件ごとに以下へ前回処理の計測結果を保存する。

```text
cases/<case_id>/dossier/opd_timing.json
```

`/case/<case_id>/dossier/opd/index` のレスポンスにも `opd_timing` として同梱され、Step 1 のドシエ情報欄に表示される。

## Timing Fields

- `operation`: 実行した処理名
- `total_sec`: 合計秒数
- `steps.fetch`: ブラウザ操作、PDF取得、ファイルコピーなど
- `steps.load_index`: 保存済み OPD インデックス読込
- `steps.parse`: 対象候補の整理、引用候補抽出など
- `steps.ocr`: OCR / PDF解析
- `steps.llm`: 翻訳・要約の LLM 呼び出し
- `steps.save`: JSON保存と再読込
- `events`: 各ステップの補助情報

## Initial Findings

現時点では実 OPD セッションを安定して再現できる案件セットがないため、定量結果は未記入。
ただし、ユーザー操作で OPD 処理を 1 回実行すると、同じ画面上で直近処理の内訳を確認できる状態になった。

想定される読み方:

- `fetch` が長い: J-PlatPat/OPD の画面遷移、ダウンロード実体探索、通信待ちがボトルネック。
- `ocr` が長い: PDF OCR / search report parser がボトルネック。ページ単位並列化または OCR キャッシュ確認を優先。
- `llm` が長い: 翻訳・要約の呼び出しがボトルネック。文書バッチ化、並列化、または保存済み要約の再利用を優先。
- `save` が長い: JSON再構築や二重読込がボトルネック。

## Next Measurement Targets

実測時は次の 3 パターンを比較する。

1. 本願内ISR OCRのみ
2. OPD書類収集後、添付PDF 1 件を保存/OCR
3. OCR済みテキストから翻訳・要約

## Candidate A-1 Actions

計測結果に応じて、次の優先順位で改善する。

1. `fetch` が支配的なら、保存済み `opd_download_signals.json` からの直接レシピ取得をさらに優先し、UIクリック経路を最後の手段にする。
2. `ocr` が支配的なら、PDFページ単位または文書単位の並列OCRと、既存 `opd_pdf_reports.json` のキャッシュ判定を強化する。
3. `llm` が支配的なら、拒絶理由・見解文書をまとめたバッチ要約、または複数プロバイダの並列実行を検討する。
4. どれも分散している場合は、バックグラウンド化と進捗APIで体感待ち時間を下げる。
