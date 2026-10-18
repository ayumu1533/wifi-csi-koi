# 案A「寄る」（引力）: 入浴者のゾーン中心に鯉の群れがゆるやかに集まる
# TDでは Text DAT「mode_A_attract」がこのファイルと同期し、koi_school_exec.py から module として呼ばれる。
# 粗粒度との適合: ゾーン中心へ寄せるだけなのでゾーン内の細かい位置はいらない。
#                 境界での分類の揺れは「鯉が方向を迷う」動きとして吸収される。

import math

LABEL = '寄る（引力）'
REACTS = True    # 人に反応する（False にすると人がいないときと同じ動き）

ATTR_W = 4.0     # 目標（人のいる位置）へ向かう力の強さ
GATHER_R = 0.25  # 目標からこの距離より内側では引き寄せない（目標のすぐそばでゆったり漂う）
SLOW_R = 0.7     # 目標からこの距離より内側では速さを落とす（通り過ぎずに留まる＝餌に群がるような動き）
SLOW_MIN = 0.45  # 目標の真上での速さの倍率（いちばん遅いとき）
SEP_DROP = 0.3   # 人がいるとき、分離（仲間から離れる）の半径をこの割合だけ小さくする（狭い角にも密に集まれるように）


def respond(f, tgt, pres, env):
	# 戻り値は (旋回量, 速さの倍率)。env は共通の値（TUB, KEEP, BOOST, steer_to）
	dx, dz = tgt['x'] - f['x'], tgt['z'] - f['z']
	d = math.hypot(dx, dz)
	turn = 0.0
	if d > GATHER_R:
		k = min(1.0, (d - GATHER_R) / 0.5)
		turn = env['steer_to'](f['th'], math.atan2(dx, dz)) * ATTR_W * pres * k
	if d < SLOW_R:
		# 目標の近くでは速さを落として留まる
		boost = 1.0 - (1.0 - SLOW_MIN) * pres * (1.0 - d / SLOW_R)
	else:
		# 目標から遠いほど少し速く泳ぐ（最大 BOOST 倍）
		boost = 1.0 + (env['BOOST'] - 1.0) * pres * min(1.0, (d - SLOW_R) / 1.0)
	return turn, boost


def separation_scale(pres):
	# 分離の半径に掛ける倍率（人がいるほど小さく）
	return 1.0 - SEP_DROP * pres


def wall_home(tgt, pres, env):
	# 壁に近づいた鯉を向かわせる点。人がいるときは浴槽の中心ではなく人の位置へ寄せる
	# （中心へ向けると、角のゾーンにいる人から鯉を引き離してしまうため）
	c = env['TUB'] / 2
	return c + (tgt['x'] - c) * pres, c + (tgt['z'] - c) * pres
