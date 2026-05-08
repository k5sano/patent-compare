# XCBKM B64 Paster Stream Deck Plugin

PDF-XChange Editor のブックマークエクスポートファイルを Stream Deck のプロパティインスペクタへドラッグアンドドロップし、Base64 文字列として保持します。ボタンを押すと、保持した Base64 を Windows のキーボード入力エミュレーションで入力します。

## 機能

- `.xcbkm` などの任意ファイルをドラッグアンドドロップで Base64 化
- アクション設定に Base64、ファイル名、サイズ、打鍵間隔を保存
- 1ms から 10ms の打鍵間隔をスライダーで調整
- 保持成功後、Stream Deck ボタンに赤丸と現在の遅延を表示

## ビルドとインストール

PowerShell でこのフォルダをカレントにして実行します。

```powershell
.\install.ps1
```

インストール先:

```text
%APPDATA%\Elgato\StreamDeck\Plugins\com.patentcompare.xcbkm-b64-paster.sdPlugin
```

Stream Deck アプリが起動済みの場合は、インストール後に Stream Deck アプリを再起動してください。アクションリストには `PatentCompare` カテゴリの `Paste XCBKM B64` として表示されます。

## 使い方

1. Stream Deck アプリで `PatentCompare` カテゴリから `Paste XCBKM B64` をボタンへ配置します。
2. 右側の設定欄へ `.xcbkm` ファイルをドラッグアンドドロップします。
3. 打鍵間隔を 1ms から 10ms の範囲で調整します。
4. 保持に成功すると、ボタン表示が赤丸になります。
5. PDF-XChange Editor など貼り付け先を前面にして、Stream Deck のボタンを押します。

約 6,000 文字の Base64 は、1ms でも数秒以上かかります。貼り付け先が長いキーボード入力を受け切れるよう、処理が終わるまで他の操作をしないでください。
