# J-PlatPat 本願書誌情報 取得 API 偵察メモ

対象: `特開2024-108988`

実行:

```powershell
python tools/recon.py --jplatpat-biblio 特開2024-108988 --out scratch/jplatpat_biblio_detail_recon.ndjson
python tools/recon.py --summarize scratch/jplatpat_biblio_detail_recon.ndjson
```

## 発見した内部 API

1. `POST /web/patnumber/wsp0102`
   - 番号照会 API。
   - `SEARCH_RSLT_LIST[0]` に `ISN`, `PUBLI_NUM_DISP`, `REG_NUM_DISP`, `APP_NUM_DISP`, `APP_DATE`, `INVEN_NAME`, `APPN_RIGHT_HOLDER`, `FI` が入る。
   - 公開番号の `NUM_TYPE`: `PUBLI_NUM_PUB_NUM_A`
   - 登録番号の `NUM_TYPE`: `PATENT_NUM_B_PATENT_INVENT_DESCRIPT_NUM_C`

2. `POST /app/comdocu/wsp1101`
   - 公報詳細表示の初期化 API。
   - `DOCU_KEY`, `ISN` を渡す。

3. `POST /app/comdocu/wsp1201`
   - 公報詳細の書誌欄を返す API。
   - `DOCU_DATA.TEXT_DATA` に `(21) 出願番号`, `(22) 出願日`, `(71) 出願人`, `(72) 発明者`, `【国際特許分類】`, `【ＦＩ】`, `【テーマコード（参考）】`, `【Ｆターム（参考）】` が HTML 断片として含まれる。

## 実装方針

- ブラウザは不要。`requests.Session` で固定 URL を GET してセッションを作り、上記 API を順に直接呼ぶ。
- `wsp1201.DOCU_DATA.TEXT_DATA` を `<br>` 区切りのテキストに正規化し、書誌事項と分類コードを抽出する。
- 失敗時は `services.case_service.create_case` 側で握り、従来どおり PDF 取得・PDF 抽出だけで案件作成を続ける。
