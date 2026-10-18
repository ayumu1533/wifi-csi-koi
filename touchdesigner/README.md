# touchdesigner

TouchDesignerプロジェクト一式（TouchDesigner 2025）。浴槽（3m×3m）を真上から見た鯉の群れを描き、
OSCで受け取った「人のいる位置」に応答設計（案A・B・C／対照N）で反応させる。

## どのファイルを開くか

| ファイル | 用途 | 案の決まり方 |
| --- | --- | --- |
| `dev_koi_projection.toe` | **開発・調整用**（ふだんはこれ） | `koi_mode` の1文字。OSC `/koi/mode`（osc_manual の `mode B`）で切り替え可 |
| `exp_A_attract.toe` | 実験条件A「寄る」 | ファイル名で固定（切り替え不可） |
| `exp_B_disperse.toe` | 実験条件B「分散」 | ファイル名で固定 |
| `exp_C_orbit.toe` | 実験条件C「周回」 | ファイル名で固定 |
| `exp_N_noreact.toe` | 実験条件N「反応なし」（対照） | ファイル名で固定 |

- 5ファイルとも同じ `scripts/` と `shaders/` を参照する。動きや見た目を直すと全条件に反映される。
- `xxx.1.toe` `xxx.2.toe` … はTDが保存のたびに自動で作る番号付きコピー。最新は番号のないファイル。古くなったら `_archive/old_toe/` へ。
- 今どの案で動いているかは `koi_input_state` の `mode` 行で確認できる。

## フォルダ構成

| 場所 | 中身 |
| --- | --- |
| `scripts/koi_school_exec.py` | 群れの共通の動き（Boids・壁よけ・速さ・「気づき」） |
| `scripts/modes/mode_*.py` | 案ごとの人への反応（A寄る／B分散／C周回／N反応なし） |
| `scripts/osc_in_callbacks.py` | OSC受信 |
| `scripts/step*.py` | 組み立てスクリプト（Textportで実行） |
| `scripts/save_stage.py` | 段階保存（`stages/` へ） |
| `scripts/_old/` | 使わなくなった確認用スクリプト |
| `shaders/` | 泳ぎ・色のGLSL |
| `stages/` | 実装段階ごとの保存版（stage1_lighting, stage2_depth, stage3_varieties） |
| `_archive/old_toe/` | 旧版・番号付きコピーの .toe |
| `_archive/debug/` | 開発中の確認用画像・ログ |
| `Backup/` `TDImportCache/` | TDが自動で作るもの（触らない） |

## ネットワーク構成（ルート `/`）

| ノード | 役割 |
| --- | --- |
| `koi_static` | FBX COMP。`assets/koi_model/koi_static.fbx` のメッシュ読み込み元（描画はしない） |
| `koi_school_exec` | Execute DAT。毎フレーム `scripts/koi_school_exec.py`（群れの動き）を実行 |
| `koi_school` | Table DAT。1匹1行で tx, tz, ry, bend |
| `koi_school_geo` | Geometry COMP。`koi_school` の行数だけ鯉をインスタンス描画（曲がりはカスタム属性0） |
| `koi_swim_mat` | GLSL MAT。泳ぎの変形（POPの属性は `TDPos()` / `TDTexAttrib_Tex()` で読む） |
| `cam_top` / `render_top` / `koi_out` | 真上からの平行投影カメラ（幅3m）／描画（1080×1080, 黒背景）／最終出力 |
| `osc_in` | OSC In DAT（ポート9000）。コールバックは `scripts/osc_in_callbacks.py` |
| `koi_input_state` | 今の入力（人がいる度合い・目標位置・送り元）の表示 |

座標: TDの X-Z 平面 = `server/config/zones.json` の (x, y) [m]。原点が浴槽の角、(1.5, 1.5) が中心。

## 入力の切り替え（手動テスト ⇔ 本番のリアルタイム推定）

TD側は変更不要。**送り元をどちらか一方だけ起動する**（同じ形式・同じ送り先 127.0.0.1:9000）。

```bash
cd server
# 手動テスト: キーボードでゾーンを指定して送る（5 / 0 / 5 6 / 5:0.7 6:0.3 / tour 10 / q）
.venv/Scripts/python.exe -m tools.osc_manual
# 本番: CSIからリアルタイム推定して送る
.venv/Scripts/python.exe -m localization.estimate --serial COM3 --model data/model.pkl --osc 127.0.0.1:9000
```

- 鯉の目標の決め方は `scripts/osc_in_callbacks.py` 冒頭の `TARGET_FROM` で切り替える（保存すると自動で反映される）。
  - `'zone'`（既定、2026-10-11〜）: **確定ゾーン**（`/koi/zone`）の中心に寄る。確定ゾーンが empty(0) なら「人がいない」（目標位置は動かさない）。
    推定の確率がばらけていても、確定ゾーンへまっすぐ寄る。確定ゾーンの切り替わりの速さは estimate の `--hysteresis-frames` / `--hysteresis-ratio` で調整する。
  - `'probs'`: 人がいる度合い = 1 − p0、目標位置 = 確率で重み付けしたゾーン中心（`/koi/zone_probs`）。
    境界で迷う動きが出るが、確率がばらけると目標が浴槽の真ん中に寄ってしまう（2026-10-11の推定で確認）。
  - どちらを使っているかは `koi_input_state` の `target_from` 行で、今の目標は `target_x` / `target_z` 行で確認できる。
- 3秒OSCが来なければ「人がいない」とみなし、鯉はゆっくり散る。
- 推定中のモードの切り替え: `localization.estimate` を `--remote --osc 127.0.0.1:9000` で起動すると、スマホの画面に A/B/C/N のボタンが出る。押すと `/koi/mode` が送られる（`exp_*.toe` を開いているときは切り替わらない）。

## 組み立てスクリプト（Textportで実行。何度実行しても作り直すだけ）

```python
exec(open(project.folder + '/scripts/step2_swim.py', encoding='utf-8').read())    # 泳ぎのGLSL MAT
exec(open(project.folder + '/scripts/step3_school.py', encoding='utf-8').read())  # 群れ（インスタンス）
exec(open(project.folder + '/scripts/step4_osc.py', encoding='utf-8').read())     # OSC受信
exec(open(project.folder + '/scripts/step8_conditions.py', encoding='utf-8').read())  # 応答設計A/B/C/N と案ごとの .toe
```

群れの動きの調整は `scripts/koi_school_exec.py` 冒頭の定数（匹数N、速さ、群れのルールなど）。人への反応の強さは案ごとの `scripts/modes/mode_*.py`。

## 応答設計（案A・B・C＋対照N）

群れの共通の動き（Boids・壁よけ・泳ぎ）は `scripts/koi_school_exec.py`、**人への反応だけを案ごとのファイル**に分けている（梗概 表2）。

| 案 | ファイル（Text DAT名も同じ） | 実験用 .toe | 反応 |
| --- | --- | --- | --- |
| A 寄る | `scripts/modes/mode_A_attract.py` | `exp_A_attract.toe` | ゾーン中心へ向かう引力。近くまで来たら止める（`ATTR_W`, `GATHER_R`） |
| B 分散 | `scripts/modes/mode_B_disperse.py` | `exp_B_disperse.toe` | 半径 `REPEL_R` 内の鯉が外向きへ。結合を弱めてばらけ、壁際では人の反対側へ逃がす |
| C 周回 | `scripts/modes/mode_C_orbit.py` | `exp_C_orbit.toe` | 半径 `ORBIT_R` の円を同じ向きに回る。角のゾーンでは円が収まるよう中心を内側へずらす |
| N 反応なし | `scripts/modes/mode_N_noreact.py` | `exp_N_noreact.toe` | 泳ぐが人に反応しない（非インタラクティブ対照条件） |

- 実験用の4つの .toe はファイル名（`exp_` の次の1文字）で案が決まり、スクリプト・シェーダーは同じファイルを参照する。
  見た目や群れの調整は共通のファイルを直せば4条件すべてに反映され、条件間で「反応」以外が変わらない。
- 案ごとの強さ・範囲は各 `mode_*.py` 冒頭の定数で調整する（TDが保存で自動反映）。
- 実行中の切り替え: `koi_mode` を直接書き換えるか、OSC `/koi/mode ['A'|'B'|'C'|'N']`（`tools.osc_manual` で `mode B`）。
  今の案は `koi_input_state` の `mode` 行で確認できる。
- 組み立て（Text DAT作成と4つの .toe 保存）は `scripts/step8_conditions.py`（上の組み立てスクリプト参照）。
- 案Aの動きは分割前と同一（同じ乱数で同じ軌跡になることをPythonで確認済み）。
