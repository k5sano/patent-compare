# Local AI Setup (RTX 3080)

## 確認結果

- GPU: NVIDIA GeForce RTX 3080
- VRAM: 10GB
- Ollama: 0.23.2
- 2026-05-12 時点の Ollama 安定版 Latest: 0.23.2

## 推奨モデル

RTX 3080 10GB では、通常作業の既定は `qwen2.5:7b-instruct` が安全です。
軽い要約、整形、抽出、下書きに使います。

用途別:

- 既定: `qwen2.5:7b-instruct`
- 少し品質優先: `qwen2.5:14b`
- コード下書き: `qwen2.5-coder:14b`
- Gemma4 検証用: `gemma4:e2b`
- 埋め込み: `bge-m3`

`qwen2.5:14b` と `qwen2.5-coder:14b` は動きますが、長いプロンプトではVRAMを超えて
CPU/RAM側に逃げることがあります。実務の待ち時間を考えると、local-ai の既定は 7B にします。

`gemma4:e2b` は RTX 3080 10GB でスモーク確認済みです。`gemma4:e4b` / `gemma4:latest`
はモデル本体が大きく、KV cache込みではVRAMを超える可能性が高いため常用候補から外します。

## セットアップ

```powershell
ollama pull qwen2.5:7b-instruct
ollama pull qwen2.5:14b
ollama pull qwen2.5-coder:14b
ollama pull gemma4:e2b
```

必要なら環境変数で既定モデルを変えます。

```powershell
$env:LOCAL_AI_MODEL = "qwen2.5:7b-instruct"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
```

疎通確認:

```powershell
python scripts\local_ai_smoke.py
```

## patent-compare 側の指定

既存の `call_claude(...)` 入口で以下のモデル名を指定できます。

- `local-ai`
- `local-qwen7b`
- `local-qwen14b`
- `local-coder14b`
- `local-gemma4-e2b`
- `ollama:<Ollama model name>`

例:

```python
call_claude("短く要約して", model="local-ai", timeout=120)
call_claude("整形して", model="ollama:qwen2.5:14b", timeout=180)
```

## 運用ルール

local-ai は低リスクな要約、整形、下書き、抽出だけに使います。
対比判断、拒絶理由対応、引用文献の法的評価など、誤りが直接実務判断に影響する箇所は
Claude/Codex/GLM または人間確認を優先します。
