# 鯉1匹を 3m×3m の浴槽の中でゆったり泳がせる
# Execute DAT（koi_motion_exec）の onFrameStart から毎フレーム呼ばれる。
# 結果は Constant CHOP「koi_motion」に書き込み、鯉の位置・向き・体の曲がりがそれを参照する。
#
# 座標: TDのX-Z平面（上から見た浴槽）。原点が浴槽の角、(1.5, 1.5) が中心。単位m。
# 向き th: 鯉の頭が +Z を向いているときを 0。+X 側へ回ると正（FBX COMPの Rotate Y と同じ向き）。

import math
import random

TUB = 3.0        # 浴槽の一辺 [m]
MARGIN = 0.8     # 壁からこの距離に入ったら中心のほうへ向きを変え始める [m]
KEEP = 0.25      # 壁にこれ以上は近づけない（体の半分） [m]
SPEED = 0.18     # 泳ぐ速さ [m/s]（ゆったり）
MAX_TURN = 0.9   # 旋回の速さの上限 [rad/s]
WANDER = 0.8     # ふらつきの強さ（大きいほど気まぐれに向きを変える）
BODY = 0.5       # 体長 [m]（曲がり具合の計算に使う）


def wrap(a):
	# 角度を -π〜π に収める
	return (a + math.pi) % (2 * math.pi) - math.pi


def get_state():
	# 位置・向きなどの状態はDATのstorageに保存する（フレームをまたいで保持）
	s = me.fetch('koi', None)
	if s is None:
		s = {'x': TUB / 2, 'z': TUB / 2, 'th': random.uniform(-math.pi, math.pi),
			 'w': 0.0, 'wt': 0.0}
		me.store('koi', s)
	return s


def onFrameStart(frame):
	s = get_state()
	dt = 1.0 / max(project.cookRate, 1)

	# 1) ふらつき: 目標の旋回速度がゆっくりランダムに変わる（0に戻ろうとする力つき）
	s['wt'] += random.uniform(-1, 1) * WANDER * 3 * dt
	s['wt'] *= 0.985
	w_target = s['wt']

	# 2) 壁よけ: いちばん近い壁までの距離が MARGIN より小さいほど、中心へ向ける力を強くする
	d = min(s['x'], s['z'], TUB - s['x'], TUB - s['z'])
	if d < MARGIN:
		k = (MARGIN - d) / MARGIN
		to_center = math.atan2(TUB / 2 - s['x'], TUB / 2 - s['z'])
		w_target += wrap(to_center - s['th']) * 4.0 * k

	# 3) 旋回速度を目標へなめらかに近づける（急に向きを変えない）
	w_target = max(-MAX_TURN, min(MAX_TURN, w_target))
	s['w'] += (w_target - s['w']) * min(1.0, 2.0 * dt)

	# 4) 向きを回して、頭の方向へ進む
	s['th'] = wrap(s['th'] + s['w'] * dt)
	s['x'] += SPEED * math.sin(s['th']) * dt
	s['z'] += SPEED * math.cos(s['th']) * dt
	s['x'] = min(max(s['x'], KEEP), TUB - KEEP)
	s['z'] = min(max(s['z'], KEEP), TUB - KEEP)

	# 5) 体の曲がり: 半径Rの円を泳ぐとき、体長Lの後ろ側は L²/(2R) だけ内側にずれる。
	#    1/R = 旋回速度/速さ。内側（曲がる方向）へ尾を寄せる。
	bend = (BODY * BODY / 2) * (s['w'] / SPEED)
	bend = max(-0.08, min(0.08, bend))

	# 6) Constant CHOP に書き出す
	c = op('/koi_motion')
	c.par.const0value = s['x']
	c.par.const1value = s['z']
	c.par.const2value = math.degrees(s['th'])
	c.par.const3value = bend
	return
