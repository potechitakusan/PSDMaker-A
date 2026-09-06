# 実用レイヤー数のPSD工程

通常のイラストを、元絵由来の線、意味／素材パーツ、パーツ内の下塗り・影・光へ整理する工程。目安は50〜100項目（描画レイヤー＋フォルダ）である。

## 入力からの操作

線画がまだなければ内蔵の画像生成AI機能で参照画像から作成し、別ファイルへ保存する。参照画像と線画を確認し、サイズ差と局所ずれをprepare-compactで補正する。alignment.jsonと補正前後の画像を実際に確認し、残る差を記録してから意味分類・PSD構築へ進む。元絵は変形しない。

作業開始時刻、フェーズ時刻、完了時刻と経過時間（秒数および何分何秒）をjobのtiming.jsonへ記録する。メッセージ受信時刻が取得できない場合、最初の作業時刻を起点として明示する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent monitor --psd output/<name>/output.psd --job work/<name>
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-compact --reference input/reference.png --lineart input/lineart.png --job work/<name>
```

`start_preview.bat` は解析前から用意しておく。利用者はいつでも起動できる。

prepare-compactは局所光学フローで線画ガイドを位置合わせし、元絵の暗い細線から線の位置と色を回収する。元絵は変形しない。線下の下塗りは狭い範囲で補間し、回収した線のRGBAとの合成が元絵に一致するよう解く。ガイドにしかない線をそのまま描き足す処理ではない。

生成物: alignment.json、alignment_comparison.png、aligned_lineart_rgba.png、aligned_lineart_white.png、underpainting.png、compact_arrays.npz、geometry_review_*.png。

## 意味別の計画

geometry_reviewの各ページを画像ツールで確認し、`semantic_plan.json` を作成する。参照はGeometry番号。各Geometryは一度だけ割当し、semantic_idは一意にする。

画像ごとに `semantic_plan.json` を新規作成する。別の画像用の計画は参照画像ハッシュと領域番号に依存するため流用しない。

一つのGeometryに複数素材が混在している場合は、次のコマンドで色クラスタ候補を先に表示し、画像を確認してからmaterial_clustersへ割り当てる。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent review-materials --job work/<name> --geometry <geometry_id> --clusters <count>
```

計画の主な項目:

- `parts`: semantic_id、display_name、regions（Geometry番号）、必要に応じmaterial_clusters。
- `material_splits`: 同じGeometryに顔と髪等が混在するとき、Labクラスタリングで分ける。IDはGeometry番号×1000＋クラスタ番号。クラスタの色と位置を一覧画像で確認して割当する。
- `keep_largest_in_geometry` / `island_fallback`: 色だけでは髪の光を肌と混同する場合など、対象Geometry内の連続性を使う。
- `derived`: 既存パーツの近くで素材特性を使って細部を分離する。
- `max_total_layers`: フォルダを含む上限。通常は100程度を指定する。超過した場合はPSD保存前に止まり、意味の近いパーツを統合する。余分な透明レイヤーを追加して下限を満たすことはしない。
- `recover_background_leaks`: 既定true。背景の暗部を前景への漏れとみなして回収する。床の影や家具が前景に混入する画像ではfalseにして背景領域を保持し、単独表示で確認する。

境界は局所位置合わせ後のガイド領域を種に、元絵の色勾配に沿って調整する。線の隙間を通じた背景への漏れは、画像境界の背景色と近隣の素材色から補修する。小さな飛び地を除去し、ボタンのような本来小さいパーツは個別の閾値で保護する。

## 分解と検証

背景以外のBaseはパーツ内の代表色で、原画全体を貼ったレイヤーではない。Shadowは画素ごとのMultiply係数と透明度、HighlightはScreen係数と透明度を数値的に求める。グラデーションや色変化を一枚のパーツレイヤー内に保ち、色断片の数だけレイヤーが増えるのを防ぐ。

PSDの大枠はCharacter配下にBackground / Base / Shadows / Highlights / Lineart。日本語パーツ名はPSDのUnicode名として保存する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-compact --job work/<name> --output output/<name>/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent evaluate --job work/<name>
.\.venv\Scripts\python.exe scripts/verify_compact.py --job work/<name>
.\.venv\Scripts\python.exe scripts/verify_preview.py
```

`semantic_review_01.png` で各パーツの独立性、`editability_review.png` で影OFF・光OFF・線OFF・下塗りだけの状態を確認する。合成スコアが高いだけでは意味分離の正しさを証明しないため、両方を確認する。

局所修正時は対応するsemantic_planの割当/素材条件のみ変更してbuild-compact。位置補正済みのcompact_arraysは再利用できる。従来のanalyze/build/apply-repairはschema 1用の基礎工程として残している。

## 実用上の範囲

これは元絵で見えている部分を編集しやすく分離したPSD。隠れている腕・髪等を描き足す処理やLive2D用の完全パーツ展開は行わない。線は元絵から回収した色付きラスター線であり、手描きの純粋な均一線画とは異なる。Photoshop / CLIP STUDIO PAINTの実アプリ上での確認は未実施。
