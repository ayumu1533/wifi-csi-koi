# 案B「分散」（斥力）: 入浴者のゾーン中心から鯉の群れが広がる
# TDでは Text DAT「mode_B_disperse」がこのファイルと同期し、koi_school_exec.py から module として呼ばれる。
# 粗粒度との適合: 「人から離れる向き」だけで動きが決まるので、細かい位置はいらない。
#                 境界での分類の揺れは、広がる向きの小さな揺れとして吸収される。

import math

LABEL = '分散（斥力）'
REACTS = True

REPEL_R = 1.2    # 人からこの距離より内側の鯉は外向きに泳ぐ [m]
REPEL_W = 4.0    # 離れる力の強さ（近いほど強い）
COH_DROP = 0.8   # 人がいるとき、結合（仲間の中心へ寄る）をこの割合だけ弱める（群れごと逃げず、ばらけて広がるため）


def respond(f, tgt, pres, env):
	# 人から鯉へ向かう向き（＝外向き）へ曲がる。REPEL_R より外の鯉は何もしない
	dx, dz = f['x'] - tgt['x'], f['z'] - tgt['z']
	d = math.hypot(dx, dz)
	if d >= REPEL_R:
		return 0.0, 1.0
	k = (REPEL_R - d) / REPEL_R          # 人に近いほど 1 に近い
	turn = env['steer_to'](f['th'], math.atan2(dx, dz)) * REPEL_W * pres * k
	boost = 1.0 + (env['BOOST'] - 1.0) * pres * k   # 近いほど少し速く離れる
	return turn, boost


def cohesion_scale(pres):
	# 結合の強さに掛ける倍率（人がいるほど弱める）
	return 1.0 - COH_DROP * pres


def wall_home(tgt, pres, env):
	# 壁に近づいた鯉を向かわせる点。浴槽の中心ではなく「人の反対側」へ逃がす
	# （中心へ向けると人のほうへ戻ってしまい、人の近くの壁際で鯉が行き来して止まるため）
	c = env['TUB'] / 2
	return c + (c - tgt['x']) * pres, c + (c - tgt['z']) * pres
