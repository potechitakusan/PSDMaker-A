# Astra実行手順

新規制作は [編集優先工程](ARTIST_WORKFLOW.md) とCOMPACT_WORKFLOWを使う。開始時に描画枚数の範囲と色統一許容値を受け取り、お任せなら自動計算する。本書のschema 1 build/repairは従来job用。

## 開始・再開

指示を受けたら開始時刻をjobのtiming.jsonへ記録する。受信時刻が取得できない場合は最初の作業時刻と明記する。フェーズ時刻と、検証を含む完了までの経過秒数・分秒を残す。

線画がまだない場合は画像生成AI機能で参照画像から作成する。分解開始前に参照画像と線画のサイズ差・局所ずれを検査し、ずれがあれば元絵を変形せず線画側を可能な限り補正する。通常はprepare-compactを使用し、alignment.jsonと補正前後画像を確認してから意味分類へ進む。生成AIが描き直した細部など、残る差は判断記録に残す。

ユーザーの最新指示、AGENTS.md、公開手順を確認する。ローカル状態のPROGRESS.mdとRESUME.mdがあれば確認する。先に `start_preview.bat` を用意し、利用者に起動ファイルを案内する。既存batがあれば再利用する。画像解析中でも別プロセスでプレビューを開けることが要件。

Pythonはすべて `.venv/Scripts/python.exe` を使用する。以下では `PY` と略す（実際のシェル変数ではない）。初回は `python scripts/bootstrap.py`。原画像は変更しない。

```text
PY -m anime_layer_agent monitor --psd output/<name>/output.psd --job work/<name>
PY -m anime_layer_agent analyze --reference input/reference.png --lineart input/lineart.png --output work/<name>
```

`analyze` の `--output` を省略すると入力ハッシュによる `work/<job_id>` が選ばれる。同じ入力と設定での再実行は抽出を再利用する。明示指定した既存jobと入力ハッシュが違う場合は別jobを使う。

## 視覚判断

1. `overview.webp` と `geometry_regions.webp` を画像ツールで開く。
2. `contact_sheet.webp` を開く。複数ページはjob.jsonのcontact_sheetsに列挙。必要に応じてcolor_regions.webpと当該ページだけを開く。
3. `summary --job work/<name> --offset 0 --limit 20` で短い統計を取得する。大量のregions.jsonを会話へ一括出力しない。
4. `layer_plan.json` を編集する。Geometryはパーツごとにまとめ、Colorはbase/shadow/highlight等へ割り当てる。座標や画素を生成しない。
5. 全ページを確認して分類を完了したら `classification_source` を `astra_reviewed` にする。未確認の段階では `heuristic` または `partial_review` を維持する。
6. 判断理由、曖昧な領域は `docs/jobs/<job>-decisions.md` に記録する。

例（Region IDは実際の抽出結果のものだけを使用）:

```json
{
  "schema_version": 1,
  "classification_source": "astra_reviewed",
  "parts": [
    {
      "semantic_id": "hair",
      "kind": "character",
      "geometry_regions": ["G0002"],
      "base_regions": ["G0002-C02"],
      "shadow_regions": ["G0002-C01"],
      "highlight_regions": ["G0002-C03"],
      "unknown_regions": [],
      "notes": "右側の暗部を影、左側の明るい帯をハイライトと判断"
    }
  ]
}
```

すべてのGeometry/Colorを重複なく割当する。上例は部分例で、実ファイルには背景等も含める。semantic_idは自由文字列で一意。同一パーツが複数のGeometryを持つ場合は一つのpartにまとめる。

追加の役割: `rim_light_regions` / `ambient_light_regions` は光として分解。`unknown_regions` / `line_related_regions` は元の色をBase側に保持して破壊的な推測を避ける。Lineartレイヤー自体は入力線画から生成する。`shadow_blend_mode` はmultiply/normal、`highlight_blend_mode` はauto/normal/screen。

## 構築・評価

```text
PY -m anime_layer_agent build --job work/<name> --output output/<name>/output.psd
PY -m anime_layer_agent evaluate --job work/<name>
```

buildは計画を検証し、RGBAレイヤーと数値的な影/光を生成。PSDを一時ファイルで検証した後に置換する。プレビューはこの保存を検知する。evaluateは保存PSDを読み直して再合成し、referenceと比較する。計画を変更したら先にbuildが必要。

analysis.jsonの全体品質とworst_regionsを読む。数値合格でもレイヤー名・割当の意味を確認する。`quality_passed` は数値合格・意味分類未確定、`complete` はastra_reviewedかつ数値合格。いずれも実アプリ上の互換性確認とは別。

## 局所修正（最大3回）

```text
PY -m anime_layer_agent repair --job work/<name>
```

repair_request.jsonに指定された `repair_Gxxxx.webp` を開く。左から参照crop・再構成crop・差分crop。現在の割当と数値がrequestに含まれる。必要なRegionだけを変更するJSONを作る。

```json
{
  "reviewed_by": "astra",
  "assignments": [
    {"region_id": "G0002-C03", "role": "highlight_regions"}
  ]
}
```

```text
PY -m anime_layer_agent apply-repair --job work/<name> --patch work/<name>/repair_patch.json
```

apply-repairは対象領域を検証、旧計画とpatchをhistoryへ保存、再構築と評価を行う。領域抽出は再実行しない。未達ならrepairへ戻り最大3回まで。単にrepair画像を再表示するだけでは回数を消費しない。限度到達時は未達領域・数値・制限をdocsとユーザーへ報告し、完了と偽らない。

役割変更で解決できない領域分割ミスはv1の制限として記録する。新しい分割アルゴリズムの開発を、画像変換ジョブの中で際限なく続けない。

## 記録

各フェーズのjob状態は `status.json` と `docs/jobs/<job>-<pathhash>.md` に自動保存される。Astraの判断は別の `docs/jobs/<job>-decisions.md`。ジョブ終了時はPROGRESS.mdとRESUME.mdも更新し、job・入力・出力・最後の成功操作・未達事項を残す。これら、入力画像、work、outputはローカル作業データでありGitへ追加しない。

再開時、`preprocessing` / `region_extraction` なら同じanalyze、`layer_decomposition` / `psd_composition` / `built` ならbuild/evaluate、`validation`ならevaluate、`repair_review`ならcrop判断を続ける。`repair_build`で中断した場合はhistoryとlayer_planの内容を比較し、修正が適用済みか確認してからbuild/evaluateする。
