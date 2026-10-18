# 第2機能（カラー版）：任意の法線照明（開発試作）

既存prepare-lightingの代わりに法線と光方向を指定できる。下塗り・意味計画・素材別マスク・入力線画は既存着色工程で確定しておく。本体はTorchなしでNPYを処理する。

```powershell
.\.venv\Scripts\python.exe -m venv .tmp/normals-venv
.\.tmp\normals-venv\Scripts\python.exe -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
.\.tmp\normals-venv\Scripts\python.exe -m pip install diffusers==0.35.1 transformers==4.56.2 accelerate==1.10.1 Pillow numpy safetensors
.\.tmp\normals-venv\Scripts\python.exe scripts/infer_marigold_normals.py --image work/<job>/flat_preview.png --output work/<job>/normals
.\.venv\Scripts\python.exe -m anime_layer_agent prepare-normal-lighting --job work/<job> --normals work/<job>/normals/normals.npy
```

上例はWindows/NVIDIAの今回実測環境。GPU版Torchは環境に合わせる。モデルは公式[prs-eth/marigold-normals-v1-1](https://huggingface.co/prs-eth/marigold-normals-v1-1)。推定スクリプトは公式repoのrevisionを取得して固定し、fp16 safetensorsだけをロードする。4steps、ensemble=1、seed=42、長辺768pxで処理し、元サイズへ出力する。元画像は変形しない。モデルカード、revision、画像パス、サイズ、初回/2回目の時間、PyTorchのVRAMピークとデバイス空き容量をinference.jsonへ保存。キャッシュはプロジェクト内.tmp/huggingface。本体・ComfyUIの依存は変更しない。

モデルカードのライセンス宣言はCreativeML Open RAIL++-M（openrail++）。モデルrepoにLICENSEファイルはなく、カードが参照するSD2ライセンスリンクは今回401。宣言とリンクをMODEL_CARD.md/MODEL_LICENSE.txtへ保存する。Stability AIの[同名ライセンス本文](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/raw/main/LICENSE.md)も参照可能だが、SD2版と完全一致すると断定しない。モデルの再配布・サービス提供は今回行わない。依存の正確なバージョンは検証出力のfreezeを参照。

normals.pngと固定下塗りを実際に開き、輪郭・顔・髪の局所的な推定差を確認する。サイズ一致だけで正しい法線や位置合わせとは判断しない。lighting.reviewed=trueと具体的なnotesをsemantic_plan.jsonへ記録してからbuild-colored。右上はlightのXを+0.65にする。他の値は同じにして光方向のみ比較する。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent build-colored --job work/<job> --output output/<job>/output.psd
.\.venv\Scripts\python.exe -m anime_layer_agent post-review --job work/<job>
# 全overview・色替えsheet・素材仮色・tones画像を開き、所見を記録
.\.venv\Scripts\python.exe -m anime_layer_agent finish-review --job work/<job>
```

落ち影はN·Lに含まれない。前髪→額、顎→首、手→衣装などは奥行きと遮蔽を要し、今回の試作では生成しない。目の反射・頬の固有赤み・アニメ的な髪の光帯も法線だけでは復元しない。下塗り入力は平坦な法線になりやすく、陰影付き入力は元照明を形状として誤解し得る。線の周辺補間は同素材から行うが、線を溝と誤認した法線が2pxより遠くに広がる場合は残る。

光ランプは芸術的な拡散明暗で、鏡面BRDF・物理的露出や色管理は扱わない。素材Baseを維持することと、元絵の明部の印象を再現することは別。既存ガイド方式や手描きの落ち影・瞳の光と併用する設計が次の候補だが、今回のCLIはガイド/法線の選択方式。

今回の開発試験は公開examples/coloring/output.psdをコピーし、既存35前景+背景のBase alphaと線を継承した。同じポーズ・画素格子の試験に限ったマスク継承であり、新線画への領域コピーには使わない。準備はscripts/prepare_normal_fixture.py、完成色入力の比較はscripts/run_normal_comparison.py、実PSDの保持検証と比較画像はscripts/report_normal_comparison.py。既存の完成試験jobは上書きせず、新しいjobを使う。

## 明暗が弱い場合の素材別色階調（2026-10-11）

従来の `tone_mapping: absolute` は影最大70%・光最大55%で、中立N·L=.65。正面法線と斜めの光では明部が少なく、影寄りBaseの白布・髪や暗部Baseの黒布は元絵の通常の明部へ届きにくい。強度だけ増やすと肌が沈む。任意の `lighting.normal_curve` に `shadow_gain` / `highlight_gain`（0〜4）と `gamma`（.25〜4、1未満で弱い明暗を強調）を指定できる。未指定は従来結果を維持する。

法線の色合わせには `lighting.color_model: source_tones` と `lighting.tone_mapping: source_ramp` を選ぶ。線近傍を除いた同paletteのN·Lの9/50/70/87.5/97.25%点を、参照のshadow/midtone/light/bright/peak RGBへ区分線形補間する。素材内の順位を合わせる芸術的な処理で、物理照明や原画の影位置の復元ではない。各素材32画素以上・全5色帯が必要。法線幅.08未満は従来方式へ戻す。曲線の節点間隔も最低.08とし、微小ノイズの極端な増幅を抑える。analysis.normal_mappingに補正点とfallback理由を保存する。

色差のある影・明部を実現するため、このモードのMultiply/Screenは画素別RGB＋透明度を使う（従来の固定色照明は維持）。RGBをBaseとの差から二層へ分解し、Base・線・マスク・レイヤー予算は保持。照明のRGBは参照JSONの色帯からだけ取得し、AI画像を貼り付けない。strength=1で測定色へ、0で下塗りへ戻る。ガイド向け `source_levels` は法線では使えない。source_rampの未対応素材は従来照明へ戻るため、必ず小パーツも確認する。

比較の実行は `scripts/compare_normal_tones.py`（既存開発jobを入力とする専用試験、新jobへ保存）。再測定は `--report-only`。比較画像・色帯・数値・imagegenプロンプト・検証はoutput/normal_tone_validation。元絵の抽出色帯と、同ポーズ参照着色PSDのtone-reviewを区別する。色帯との一致は補正に使用した情報への適合であり、未知画像での精度や影形状の品質を証明しない。imagegen画像は比較用に留める。

## 50光源の選択

prepare-normal-lightingは方向省略時に1番、左上手前(-.65,+.65,.55)を使う。--preset 1〜50と任意--light X Y Zは排他。X右・Y上・Z手前で、49方向は正規化して用いる。

| 番号 | 選択肢 |
|---|---|
| 1〜9 | 左上手前、中心とXY各±.2の周囲8点。1番が既定 |
| 10〜18 | 右上手前、同じ9点 |
| 19〜27 | 左下手前、同じ9点 |
| 28〜36 | 右下手前、同じ9点 |
| 37〜39 | 上手前、XY距離.85/.55/.25の3点 |
| 40〜42 | 下手前、同じ3点 |
| 43〜45 | 左手前、同じ3点 |
| 46〜48 | 右手前、同じ3点 |
| 49 | 中央手前(0,0,1) |
| 50 | 法線なし、Baseと固定入力線だけ（影/光レイヤーなし） |

四隅36＋軸12＋中央1＋法線なし1＝50。斜め群の中心はXY各±.65、Z=.55。軸もZ=.55。中央へ近づけると光が正面に近づく。

start_preview.batの「光源プレビュー」で50種類を選ぶと「照明」表示を計算する。保存PSD・jobを変更しない比較表示で、PSD保存と同じ計算式を共有する。非法線jobやjob未登録の手動PSDでは選択を無効にする。計算は別スレッド、直近3枚だけをキャッシュし、古い選択の結果を現在の表示へ混ぜない。

```powershell
.\.venv\Scripts\python.exe -m anime_layer_agent list-normal-lights
.\.venv\Scripts\python.exe -m anime_layer_agent select-normal-light --job work/<job> --preset 10
# 選択方向のプレビューをAstraが確認してreviewed/notesを記入
.\.venv\Scripts\python.exe -m anime_layer_agent build-colored --job work/<job> --output output/<job>/output.psd
# post-review→全画像確認→finish-review
```

select-normal-lightはsemantic_planへ番号/方向/enabledを記録し、照明確認を未完了へ戻す。プレビューで見た選択を保存PSDへ反映するときはこのコマンドと通常の再構築・レビューを使う。50番でも法線パスを保持し、他の光源へ戻せる。法線なしのbuild-coloredはBaseと線だけを出力する。
