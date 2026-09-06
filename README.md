# PsdMaker

完成イラストと対応する線画を、編集可能なPSDへ分解するローカルPythonツールです。Codexは画像の意味と領域の役割を判断し、Pythonが位置合わせ、マスク、レイヤー、PSD、再構成評価を処理します。

リポジトリにはソースコード、テスト、利用手順のみを含めます。入力画像、生成PSD、解析キャッシュ、プレビュー状態は各利用者のローカル環境にだけ作成され、Gitでは無視されます。

## セットアップ

Python 3.12以上（Tkinterを含む）と、初回インストール時のネット接続が必要です。リポジトリ直下で次を実行します。

```powershell
.\setup.bat
```

依存関係はプロジェクト内の `.venv` にインストールされます。

## 新しい画像を処理する

1. `input` フォルダを作成し、完成画像を `input/reference.png`、線画を `input/lineart.png` として置きます。線画がない場合はCodexに作成を依頼できます。
2. `start_preview.bat` をダブルクリックすると、別プロセスのプレビュー画面が起動します。PSDが未作成の間は待機します。
3. Codexへ次のように依頼します。

```text
AGENTS.mdとdocs/COMPACT_WORKFLOW.mdを読み、input/reference.pngとinput/lineart.pngから
output/my_job/output.psdを作成してください。プレビュー監視先を登録し、線画の位置合わせ、
意味分類、PSD生成、評価まで進めてください。
```

通常は [コンパクト工程](docs/COMPACT_WORKFLOW.md) を利用します。画像ごとに意味分類を確認して、必要なパーツだけをBase、Shadow、Highlight、Lineartとして分離します。

## CLI

PowerShellでリポジトリ直下から実行します。`<name>` は任意のジョブ名に置き換えてください。

```powershell
# プレビューが監視する出力先を登録
.\.venv\Scripts\python.exe -m anime_layer_agent monitor --psd output/<name>/output.psd --job work/<name>

# 推奨工程: 線画を位置合わせして領域候補を生成
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-compact --reference input/reference.png --lineart input/lineart.png --job work/<name>

# semantic_plan.json を画像ごとに作成・確認した後、PSDを構築・評価
.\.venv\Scripts\python.exe -m anime_layer_agent build-compact --job work/<name> --output output/<name>/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent evaluate --job work/<name>
```

基礎CLI（`analyze` / `build` / `evaluate` / `run`）も利用できます。詳細は [Astraの作業手順](docs/AGENT_WORKFLOW.md) と [仕様](docs/SPEC.md) を参照してください。

終了コードは、`0` が成功、`1` が入力または実行エラー、`2` が処理は成功したものの数値品質基準未達です。

## プレビュー

対象を固定して起動する場合は、次のように指定します。

```powershell
.\start_preview.bat --psd "output/<name>/output.psd" --job "work/<name>"
```

PSD、参照、差分の表示を切り替えられます。最新の監視先やジョブ記録はローカル状態として `docs/` 以下に生成されます。

## 検証

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts/verify_preview.py
```

半透明の完成画像、隠れた部分の描き足し、Live2D用の完全展開は対象外です。PhotoshopまたはCLIP STUDIO PAINTでの実アプリ互換性は、利用する環境で確認してください。
