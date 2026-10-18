# 案C「周回」（円軌道拘束）: 入浴者のゾーンの周りを、鯉の群れが同じ向きに回遊する
# TDでは Text DAT「mode_C_orbit」がこのファイルと同期し、koi_school_exec.py から module として呼ばれる。
# 粗粒度との適合: 円の中心（ゾーン中心）だけで動きが決まり、ゾーン内の位置に依存しない。
#                 周回そのものが繰り返しの動きなので、境界での揺れが目立ちにくい。

import math

LABEL = '周回（円軌道）'
REACTS = True

ORBIT_R = 0.7     # 回る円の半径 [m]
ORBIT_W = 2.5     # 円に乗る力の強さ
ORBIT_BAND = 0.35 # 円からこれだけずれたら最大の力で円へ戻す [m]（内側でも外側でも）
ORBIT_DIR = 1     # 回る向き（1 / -1。逆回りにしたいときは符号を変える）


def orbit_center(tgt, env):
	# 円の中心。人の位置のままだと角のゾーンでは円が浴槽からはみ出すので、
	# 円が壁（KEEP）の内側に収まるところまで中心を内側へずらす（角では「人のすぐ脇を通る周回」になる）
	lo = ORBIT_R + env['KEEP'] + 0.15
	hi = env['TUB'] - lo
	return min(max(tgt['x'], lo), hi), min(max(tgt['z'], lo), hi)


def respond(f, tgt, pres, env):
	# 円の中心から見た「外向き」u と、それを90°回した「接線の向き」を足して進む向きにする
	cx, cz = orbit_center(tgt, env)
	dx, dz = f['x'] - cx, f['z'] - cz
	d = math.hypot(dx, dz) + 1e-6
	ux, uz = dx / d, dz / d                      # 外向き（単位ベクトル）
	tx, tz = -uz * ORBIT_DIR, ux * ORBIT_DIR     # 接線の向き（円に沿って回る向き）
	e = max(-1.0, min(1.0, (d - ORBIT_R) / ORBIT_BAND))  # 円の外なら+、内なら−（ずれの大きさ）
	hx, hz = tx - ux * e, tz - uz * e            # 外にいれば内へ、内にいれば外へ寄せながら回る
	turn = env['steer_to'](f['th'], math.atan2(hx, hz)) * ORBIT_W * pres
	# 円から遠いときだけ少し速く泳いで円に合流する
	boost = 1.0 + (env['BOOST'] - 1.0) * pres * min(1.0, max(0.0, abs(d - ORBIT_R) - ORBIT_BAND) / 1.0)
	return turn, boost
