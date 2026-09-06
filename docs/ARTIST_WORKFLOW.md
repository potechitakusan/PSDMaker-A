# イラストレーター向け工程

新規制作はこの工程を優先する。既存のcompact jobは、`configure-editing`を実行するまで従来方式で再構築できる。入力の生成・線画の位置合わせ・視覚確認はCOMPACT_WORKFLOWに従う。

## 希望を受け取る

最初に「描画レイヤー数の範囲」と「色の統一許容値」をまとめて確認する。既に指定されている値は再質問しない。お任せ／未指定なら下記の自動値を使い、採用値と理由を伝える。必要以上に質問して制作を止めない。

- 枚数は**描画レイヤーのみ**。フォルダ数と合計項目数は別記する。例：100〜200枚。
- 色の統一許容値は0〜100。0は塗りの合成色を維持、100は強め。内部では塗り画素の変更量をCIE76色差 `値 / 5` 以下に制限する。0でも共有ベース色と影・模様への分解は行う。
- モノクロ線への変更は塗りの許容値とは別に評価する。線色の維持を希望する場合は`--lineart source`。
- 未指定の枚数は、意味確認済みの前景素材数Nから `2N+2`〜`5N+2` を目安にする。Base/Shadow/Highlightと必要な相対色補正（乗算・スクリーン）を数え、不要な透明レイヤーは作らない。pigment素材は上限を1ずつ減らす。
- 未指定の色許容値は、共有素材の代表色のばらつきから10〜40を算出する。共有素材がなければ15。Agentは結果を見て調整できる。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent configure-editing --job work/<name> --layer-range auto --color-tolerance auto
# 明示する例
.\.venv\Scripts\python.exe -m anime_layer_agent configure-editing --job work/<name> --layer-range 100-200 --color-tolerance 30 --lighting neutral
```

## パーツと配色を計画する

意味分類済みのsemantic_plan.jsonに次を指定する。`palette_id`が同じパーツは同一のベース色を共有する。左右の瞳、同じ布、同じ髪など、**視覚的に同素材と確認したものだけ**に同じIDを付ける。名前・色だけで自動的に同一素材とは認定しない。異色の瞳や別素材のアクセントは別IDを使う。

```json
{
  "semantic_id": "sleeve_left",
  "display_name": "袖・左",
  "group_path": ["キャラクター", "衣装", "着物"],
  "palette_id": "kimono_pink",
  "regions": []
}
```

上記は構造例。regionsにはその画像で確認したIDを指定する。PSDは`キャラクター / 衣装 / 着物 / 袖・左 [sleeve_left]`内にBase、Shadow（乗算）、Highlight（スクリーン）、必要な色補正（乗算・スクリーン）を隣接配置する。PSD内では上から色補正、Highlight、Shadow、Baseになる。

影・光は既定で一定のグレーと画素別透明度。`--lighting cool`または`warm`で照明色を選べる。許容値内の色ぶれを寄せ、残る色差はBaseに応答する相対色補正へ分ける。模様や別素材のアクセントは別パーツへ分離する。意図して固定する固有色だけpartの`detail_mode: pigment`で通常レイヤーへ残す。

## 背景の混入を直す

`review-editing --job work/<name>`で、PSD保存前に現在のパーツ一覧・背景検査・設定の提案を生成できる。partial_reviewの計画でも使えるが、PSD構築はastra_reviewedを必要とする。

`background_only.png`と`background_review.png`を目視確認する。background_review.jsonの候補は、背景と色が離れた成分、Pythonが計算した種の座標、近傍パーツを示す。床影や小物も検出されるので、候補の数を意味分類の合否にしない。

明るい低彩度の背景だと確認できた画像では、計画に次の補助を指定できる。前景から24px以内で、実際の背景色より近隣前景色に近い画素だけを回収する。任意の風景や暗背景へ流用しない。採用前後の背景単独画像を確認し、残りは色選択で直す。

```json
"background_cleanup": {"mode": "reviewed_light_background", "max_distance": 24, "color_margin": 3}
```

色選択は座標原点が画像左上、xが右、yが下。プレビューに表示された原寸座標やPythonが計算した候補の種を使う。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent select-color --job work/<name> --x 120 --y 240 --source-part background --name ribbon --tolerance 15 --metric delta-e-2000 --families pink red
# selections/ribbon_preview.pngを実際に確認した後
.\.venv\Scripts\python.exe -m anime_layer_agent assign-selection --job work/<name> --selection work/<name>/selections/ribbon.json --source-part background --to ribbon
```

- 既定は4近傍の連続領域。`--connectivity 8`で対角接続、`--global-match`で離れた同色領域も選択する。
- 距離は`delta-e-2000`、`delta-e-76`、`rgb`。許容値の単位は各色差、RGBは0〜255のユークリッド距離。配色統一の0〜100とは別の操作。
- 色系統はred/orange/yellow/green/cyan/blue/purple/pink/neutral。`--hue-range 350 10`のように赤をまたぐHSV色相範囲も指定できる。
- `--source-part`で別パーツへの漏れを防ぐ。変更せず選択を見るだけなら省略可能だが、割当時は必須。
- mask PNG、着色プレビュー、RGB種色、Geometry重なり、再現条件を保存する。入力ハッシュ違い・選択の重複・範囲外の種は拒否する。
- 割当は領域抽出をやり直さず、最後に適用する。assign-selectionは旧計画をhistoryへ退避し、分類をpartial_reviewに戻す。確認後にastra_reviewedへ更新してbuild-compactする。

## 構築と仕上げ

新しいconfigure-editingは相対色補正（Multiply/Screen）を既定とし、固定Normalの色残差がBase色替えを打ち消す問題を軽減する。花柄、白目、頬の固有色などは、意味確認した別素材/パーツへ分離する。必要な固有色保持に限りpartの`detail_mode: pigment`を指定できる。線は補正済みガイド付近の元絵線を残し、裏付けの弱い小さな成分を塗り側へ戻す。どちらも万能な自動修正ではないため、POST_REVIEW_CHECKLIST.mdのセルフチェックは必須。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-compact --job work/<name> --output output/<name>/output.psd
.\.venv\Scripts\python.exe scripts/verify_compact.py --job work/<name>
.\.venv\Scripts\python.exe -m anime_layer_agent post-review --job work/<name>
# POST_REVIEW_CHECKLIST.mdと全比較画像を確認し、post_review_assessment.jsonへ所見を記入
.\.venv\Scripts\python.exe -m anime_layer_agent finish-review --job work/<name>
```

`editing_report.json`には希望値／採用値、共有パレット、塗りの最大変更色差、描画・フォルダ・合計数、枚数の達成を記録する。上限超過はPSD保存前に停止。下限未達では有用な少数レイヤーを保存して未達と報告し、水増ししない。Agentは必要な素材分離を追加するか、妥当な範囲を利用者と調整する。

`analysis.json`は原画とのMAE/SSIM/ΔE/エッジ差を従来どおり評価する。別に`editing_readback`で、意図した色調整・モノクロ化の結果とPSD読み戻しを比較する。意図した変更があっても原画品質の不合格を合格へ置換しない。枚数未達・読み戻し未達もCLI終了コード2になる。

最終的にパーツ一覧、背景、配色、線画、影OFF／光OFF／下塗りの実画像を開く。`start_preview.bat`でもPSD・参照・差分・線画・背景・配色を切り替えられる。Photoshop/CLIP STUDIOでの実アプリ互換性と、見えていない部分の展開は別の確認・作業となる。

## 同色で離れた小領域を分ける

`review-components --job work/<name> --material 16008`のように、確認した素材IDの連結成分一覧を生成する（Geometryは`--geometry`）。画像を開き、例えば白目の成分だけを選んでsemantic_planへ次を記入する。IDは例であり、別画像へ流用しない。

```json
"component_assignments": [
  {"kind":"material", "region":16008, "components":[186,187], "from":"hair", "to":"sclera"}
]
```

変更後はpartial_reviewに戻し、review-editingで結果を確認してからastra_reviewedへ進める。色選択や通常割当と同様、最終PSDの事後チェックもやり直す。
