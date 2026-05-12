# Codex 共通プリアンブル（タスク指示冒頭に必ず貼り付ける）

## 最重要ルール（CLAUDE.md / AGENTS.md の抜粋）
1. J-PlatPat は 1 秒/リクエスト以上、同一ホストへの同時接続は 2 以下、指数バックオフ必須。
2. 特許 PDF の取得は必ず `modules.patent_downloader.download_patent_pdf_smart(...)` 経由。
   `download_patent_pdf` の直接呼び出しは禁止。
3. データ取得の優先順位は「ネットワーク観測 → 内部 API 直叩き → Playwright ハイブリッド → UI 自動化」の順。
   UI 自動化は最終手段。
4. PDF は保存前に `%PDF-` マジックバイトを検証すること。
5. キャッシュは `cases/<case_id>/...` 配下に配置し、再取得を避ける。
6. HTTP は `requests`（新規の非同期モジュールのみ `httpx`）、ブラウザ自動化は Playwright に統一。
7. タイムアウトは connect 10s / read 30s、認証セッションは再利用する。
8. ファイルサイズ規律: 40KB 超過で分割検討、60KB 超過で分割必須。既存ファイルへの追記より新規ファイル作成を優先。
9. 変更は 1 タスク 1 PR。既存テストをすべてパスさせ、必要に応じてスモークテストを追加。
10. 破壊的変更を含む場合は事前に Issue で合意を取る。

## 参照
- 全体規約: ルートの `CLAUDE.md`（= `AGENTS.md`）
- 過去の設計レビュー: `docs/design_review_llm_layer_2026-05-07.md`
- OPD 性能レポート: `docs/opd_perf_report_2026-05-10.md`
- 最新ハンドオフ: `docs/handoff_2026-05-10.md`
