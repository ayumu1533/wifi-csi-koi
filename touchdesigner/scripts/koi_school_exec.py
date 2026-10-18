# 鯉の群れ（N匹）を 3m×3m の浴槽の中で泳がせる（Boidsの基本形）
# 人への反応（応答設計）は案ごとのファイル scripts/modes/mode_*.py にあり、このファイルは共通の群れの動きだけを持つ。
#   mode_A_attract.py = 寄る / mode_B_disperse.py = 分散 / mode_C_orbit.py = 周回 / mode_N_noreact.py = 反応なし
# どの案を使うかは:
#   実験用ファイル exp_A_attract.toe などで開いたとき → ファイル名の案に固定（koi_mode やOSCでは変わらない）
#   作業用ファイル dev_koi_projection.toe など → Text DAT「koi_mode」の1文字（A/B/C/N）。OSC /koi/mode でも書き換わる
# Execute DAT（koi_school_exec）の onFrameStart から毎フレーム呼ばれる。
# 結果は Table DAT「koi_school」に1匹1行で書き込み（tx, tz, ry, bend, kind）、
# Geometry COMP「koi_school_geo」がそれをインスタンスとして描く。
#
# 座標: TDのX-Z平面（上から見た浴槽）。原点が浴槽の角、(1.5, 1.5) が中心。単位m。
# 向き th: 頭が +Z を向いているときを 0。+X 側へ回ると正（Rotate Y と同じ向き）。

import math
import random
import time

N = 10           # 鯉の数
TUB = 3.0        # 浴槽の一辺 [m]
MARGIN = 0.9     # 壁からこの距離に入ったら中心のほうへ向きを変え始める [m]
KEEP = 0.25      # 壁にこれ以上は近づけない [m]
SPEED = 0.18     # 基準の泳ぐ速さ [m/s]（1匹ごとに±20%ばらつかせる）。実際の速さは下の「気づき」の倍率を掛ける
MAX_TURN = 0.9   # 旋回の速さの上限 [rad/s]（人がいないとき）
WANDER = 0.8     # ふらつきの強さ
BODY = 0.5       # 体長 [m]
BEND_GAIN = 0.5  # 曲がるときの体のしなりの強さ（1=円の通りに曲げる。小さいほど背骨がまっすぐ）
BEND_MAX = 0.035 # しなりの上限 [m]（尾の先の横ずれ）

# 群れのルール（Boids）: 半径 [m] と強さ
SEP_R, SEP_W = 0.6, 3.0    # 分離: 近すぎる仲間から離れる（ぶつからないため。いちばん強い）
ALI_R, ALI_W = 1.0, 0.6    # 整列: 近くの仲間と向きをそろえる
COH_R, COH_W = 1.2, 0.15   # 結合: 近くの仲間の中心へゆるく寄る
# 人の入力（OSC。osc_in_callbacks.py が me.store('input', ...) に入れる）
TARGET_LAG = 1.0 # 目標の位置をなめらかに追う時間 [秒]（推定が飛んでも急に向きを変えない）
PRES_LAG = 2.0   # 「人がいる度合い」をなめらかに変える時間 [秒]（出入りでゆっくり集まる・散る）
BOOST = 1.8      # 人に反応して泳ぐときの速さの倍率の上限（各案のファイルが使う）
STALE = 3.0      # OSCがこの秒数来なければ「人がいない」とみなす
# 品種ごとの匹数（合計がNを超えた分は無視、足りない分は紅白）。0=紅白 1=大正三色 2=黄金 3=丹頂
# 黄金は1匹だけにして「珍しい1匹」にする
KIND_MIX = {0: 4, 1: 3, 2: 1, 3: 2}
PUSH_R = 0.4               # これより近い2匹は、位置を少しずつ押し離す（向きの調整だけでは間に合わないため）

# ---- 気づき（人を検知したら群れ全体が活発になる。案A・B・C共通、Nでは起きない）----
# 位置の精度に関係なく「いる／いない」だけで決まるので、粗いセンシングでも確実に伝わる反応
IDLE_K = 0.7       # 誰もいないときの速さの倍率（SPEED×0.7 ≒ 0.13 m/s。ゆったり遊泳）
ACTIVE_K = 1.7     # 人がいるときの速さの倍率（SPEED×1.7 ≒ 0.31 m/s）
AROUSE_UP = 0.6    # 人を検知してから速くなるまでの時間 [秒]（すぐ気づいたと感じられるよう短く）
AROUSE_DOWN = 4.0  # 人がいなくなってから、ゆったりに戻るまでの時間 [秒]
TURN_K = 1.6       # 人がいるときの旋回の速さの上限の倍率（速くなっても目標へ曲がり切れるように）
BURST_K = 0.6      # 検知した瞬間・ゾーンが変わった瞬間に、一時的に足す速さの割合（+60%。一斉に「はっ」と動く）
BURST_TIME = 1.5   # その一時的な速さが消えるまでの時間 [秒]
BURST_GAP = 3.0    # 一斉反応の最短の間隔 [秒]（境界で推定が揺れても連発しない）
JUMP_D = 0.7       # 人の位置（推定）がこれ以上動いたら「ゾーンが変わった」とみなす [m]
V_MAX = 0.6        # 速さの上限 [m/s]

# ---- 応答設計（梗概 表2）----
# 案の記号 → 案ごとのファイルと同期している Text DAT の名前
MODE_DATS = {'A': 'mode_A_attract', 'B': 'mode_B_disperse', 'C': 'mode_C_orbit', 'N': 'mode_N_noreact'}
MODE = 'A'       # Text DAT「koi_mode」が無いときに使う案


def wrap(a):
	# 角度を -π〜π に収める
	return (a + math.pi) % (2 * math.pi) - math.pi


def steer_to(th, target_th):
	# 今の向き th から目標の向きへ回る量（-π〜π）
	return wrap(target_th - th)


def get_state():
	# 全員の状態をDATのstorageに保存する（フレームをまたいで保持）。Nを変えたら作り直す
	fish = me.fetch('school', None)
	if fish is None or len(fish) != N:
		# 品種の割り当て: KIND_MIX の数だけ並べてシャッフル
		kinds = [k for k, c in sorted(KIND_MIX.items()) for _ in range(c)][:N]
		kinds += [0] * (N - len(kinds))
		random.shuffle(kinds)
		fish = []
		for i in range(N):
			fish.append({
				'x': random.uniform(0.6, TUB - 0.6),
				'z': random.uniform(0.6, TUB - 0.6),
				'th': random.uniform(-math.pi, math.pi),
				'w': 0.0, 'wt': 0.0,
				'v': SPEED * random.uniform(0.8, 1.2),   # 1匹ごとの速さ
				'kind': kinds[i],                        # 品種
			})
		me.store('school', fish)
	return fish


def get_target(dt):
	# OSCの入力から「目標の位置」と「人がいる度合い(0〜1)」を求め、なめらかにして返す
	inp = me.fetch('input', None)
	sm = me.fetch('target', None)
	if sm is None:
		sm = {'x': TUB / 2, 'z': TUB / 2, 'pres': 0.0}
		me.store('target', sm)
	pres_raw = 0.0
	sm['raw_xz'] = None
	if inp is not None and time.time() - inp['t'] < STALE:
		pres_raw = inp['presence']
		if inp['x'] is not None:
			sm['raw_xz'] = (inp['x'], inp['z'])   # なめらかにする前の位置（ゾーンが変わった瞬間の検出用）
			# 目標の位置: TARGET_LAG 秒くらいかけて追いかける
			a = min(1.0, dt / TARGET_LAG)
			sm['x'] += (inp['x'] - sm['x']) * a
			sm['z'] += (inp['z'] - sm['z']) * a
	# 人がいる度合い: PRES_LAG 秒くらいかけて変える
	sm['pres'] += (pres_raw - sm['pres']) * min(1.0, dt / PRES_LAG)
	sm['raw'] = pres_raw
	return sm


def get_arousal(tgt, reacts, dt):
	# 「気づき」の状態を更新して、全員に掛ける速さの倍率を返す
	#   arousal: 0=だれもいない 〜 1=人に気づいている。上がるのは速く(AROUSE_UP)、下がるのはゆっくり(AROUSE_DOWN)
	#   burst  : 検知した瞬間・ゾーンが変わった瞬間に 1 になり、BURST_TIME 秒かけて 0 に戻る
	st = me.fetch('arousal', None)
	if st is None:
		st = {'a': 0.0, 'burst': 0.0, 'last_burst': -99.0, 'was_on': False, 'xz': None, 'clock': 0.0}
		me.store('arousal', st)
	on = reacts and tgt['raw'] > 0.5          # 人がいると判定しているか（Nのときは常に「いない」扱い）
	goal = tgt['raw'] if reacts else 0.0
	lag = AROUSE_UP if goal > st['a'] else AROUSE_DOWN
	st['a'] += (goal - st['a']) * min(1.0, dt / lag)

	now = time.time()
	trigger = False
	if on and not st['was_on']:
		trigger = True                            # いなかった → いる（検知した瞬間）
	elif on and tgt['raw_xz'] is not None and st['xz'] is not None:
		if math.hypot(tgt['raw_xz'][0] - st['xz'][0], tgt['raw_xz'][1] - st['xz'][1]) > JUMP_D:
			trigger = True                        # 別のゾーンへ移った
	if trigger and now - st['last_burst'] > BURST_GAP:
		st['burst'] = 1.0
		st['last_burst'] = now
		st['xz'] = tgt['raw_xz']
	elif on and st['xz'] is None:
		st['xz'] = tgt['raw_xz']
	if not on:
		st['xz'] = None
	st['was_on'] = on
	st['burst'] = max(0.0, st['burst'] - dt / BURST_TIME)

	speed_k = IDLE_K + (ACTIVE_K - IDLE_K) * st['a'] + BURST_K * st['burst']
	# 尾を振る速さも泳ぐ速さに合わせる（速く泳ぐときに体が「滑って」見えないように）
	# 時間そのものを速さの倍率で進め、シェーダーの uTime に渡す（振動数を直接変えると波が飛ぶため）
	st['clock'] += dt * speed_k
	mat = op('/koi_swim_mat')
	if mat is not None and hasattr(mat.par, 'vec0valuex'):
		p = mat.par.vec0valuex
		if p.mode != ParMode.CONSTANT:
			p.mode = ParMode.CONSTANT             # 元は absTime.seconds の式。こちらの時計に置き換える
		p.val = st['clock']
	return speed_k, st['a']


def get_mode():
	# 今の案の記号と、その案のファイル（module）を返す
	name = project.name                       # 例: 'exp_B_disperse.3.toe'
	if name.startswith('exp_') and name[4:5].upper() in MODE_DATS:
		m = name[4:5].upper()                 # 実験用ファイルは、ファイル名の案に固定
	else:
		d = op('/koi_mode')
		m = d.text.strip().upper()[:1] if d is not None else ''
		if m not in MODE_DATS:
			m = MODE                          # koi_mode の中身が A/B/C/N 以外なら MODE を使う
	me.store('mode_now', m)                   # 今の案（koi_input_state の表示用）
	return m, op('/' + MODE_DATS[m]).module


def onFrameStart(frame):
	fish = get_state()
	dt = 1.0 / max(project.cookRate, 1)
	tgt = get_target(dt)
	mode, rule = get_mode()
	# 案のファイルに渡す共通の値
	env = {'TUB': TUB, 'KEEP': KEEP, 'BOOST': BOOST, 'steer_to': steer_to}
	# 反応しない案（N）のときは、人がいても「いない」ときと同じに泳ぐ
	pres = tgt['pres'] if getattr(rule, 'REACTS', True) else 0.0
	# 結合の強さ（案Bは人がいるほど弱めて群れをばらけさせる。案のファイルに cohesion_scale が無ければそのまま）
	coh_w = COH_W * (rule.cohesion_scale(pres) if hasattr(rule, 'cohesion_scale') else 1.0)
	# 気づき: 全員の速さの倍率と、旋回の速さの上限
	speed_k, arousal = get_arousal(tgt, getattr(rule, 'REACTS', True), dt)
	max_turn = MAX_TURN * (1.0 + (TURN_K - 1.0) * arousal)
	# 分離の半径（案Aは人がいるほど小さくして、鯉どうしが近づいて密に集まれるようにする。案のファイルに無ければそのまま）
	sep_r = SEP_R * (rule.separation_scale(pres) if hasattr(rule, 'separation_scale') else 1.0)
	push_r = PUSH_R * sep_r / SEP_R

	for i, f in enumerate(fish):
		# 1) ふらつき（目標の旋回速度がゆっくりランダムに変わる）
		f['wt'] += random.uniform(-1, 1) * WANDER * 3 * dt
		f['wt'] *= 0.985
		turn = f['wt']

		# 2) 群れのルール: 近くの仲間を調べる
		sep_x = sep_z = 0.0          # 離れる方向の合計
		ali_x = ali_z = 0.0          # 仲間の向きの合計
		coh_x = coh_z = 0.0; n_coh = 0  # 仲間の位置の合計
		for j, g in enumerate(fish):
			if i == j:
				continue
			dx, dz = g['x'] - f['x'], g['z'] - f['z']
			d = math.hypot(dx, dz) + 1e-6
			if d < sep_r:
				# 近いほど強く、相手と反対方向へ
				k = (sep_r - d) / sep_r
				sep_x -= dx / d * k
				sep_z -= dz / d * k
			if d < ALI_R:
				ali_x += math.sin(g['th'])
				ali_z += math.cos(g['th'])
			if d < COH_R:
				coh_x += g['x']; coh_z += g['z']; n_coh += 1

		if sep_x or sep_z:
			k = min(1.0, math.hypot(sep_x, sep_z))
			turn += steer_to(f['th'], math.atan2(sep_x, sep_z)) * SEP_W * k
		if ali_x or ali_z:
			turn += steer_to(f['th'], math.atan2(ali_x, ali_z)) * ALI_W
		if n_coh:
			cx, cz = coh_x / n_coh, coh_z / n_coh
			turn += steer_to(f['th'], math.atan2(cx - f['x'], cz - f['z'])) * coh_w

		# 2.5) 人への反応（中身は案ごとのファイルの respond()）
		if pres > 0.02:
			r_turn, f['boost'] = rule.respond(f, tgt, pres, env)
			turn += r_turn
		else:
			f['boost'] = 1.0

		# 3) 壁よけ（壁に近いほど中心へ向ける力を強く）
		# 人がいるときは壁際の目標（角のゾーンなど）にも寄れるよう、壁よけの範囲を狭める
		margin = MARGIN * (1.0 - 0.5 * pres)
		d = min(f['x'], f['z'], TUB - f['x'], TUB - f['z'])
		if d < margin:
			k = (margin - d) / margin
			# 向かう点はふつう浴槽の中心。案のファイルに wall_home があればそれを使う（案B: 人の反対側）
			hx, hz = rule.wall_home(tgt, pres, env) if hasattr(rule, 'wall_home') else (TUB / 2, TUB / 2)
			to_center = math.atan2(hx - f['x'], hz - f['z'])
			turn += steer_to(f["th"], to_center) * 6.0 * k

		# 4) 旋回速度を目標へなめらかに近づける
		turn = max(-max_turn, min(max_turn, turn))
		f['w'] += (turn - f['w']) * min(1.0, 2.0 * dt)

	# 5) 全員の向きと位置を進める（全員の判断が終わってから動かす）
	for f in fish:
		f['th'] = wrap(f['th'] + f['w'] * dt)
		v = min(V_MAX, f['v'] * speed_k * f.get('boost', 1.0))
		f['vnow'] = v                              # 今の速さ（体のしなりの計算に使う）
		f['x'] += v * math.sin(f['th']) * dt
		f['z'] += v * math.cos(f['th']) * dt

	# 押し離し: 近すぎる2匹を、重なり量の一部だけ互いに逆向きへずらす（毎フレーム少しずつ）
	for i in range(N):
		for j in range(i + 1, N):
			a, b = fish[i], fish[j]
			dx, dz = b['x'] - a['x'], b['z'] - a['z']
			d = math.hypot(dx, dz) + 1e-6
			if d < push_r:
				m = (push_r - d) * 0.05
				a['x'] -= dx / d * m; a['z'] -= dz / d * m
				b['x'] += dx / d * m; b['z'] += dz / d * m

	for f in fish:
		f['x'] = min(max(f['x'], KEEP), TUB - KEEP)
		f['z'] = min(max(f['z'], KEEP), TUB - KEEP)

	# 6) Table DAT に書き出す（1行目は列名）
	# 品種を割り当て直したいとき（KIND_MIXを変えたとき）は、すでにいる鯉にも反映する
	want = [k for k, c in sorted(KIND_MIX.items()) for _ in range(c)][:N]
	want += [0] * (N - len(want))
	if sorted(f.get('kind', -1) for f in fish) != sorted(want):
		random.shuffle(want)
		for f, k in zip(fish, want):
			f['kind'] = k

	cols = ['tx', 'tz', 'ry', 'bend', 'kind']
	t = op('/koi_school')
	if t.numRows != N + 1 or t.numCols != len(cols):
		t.setSize(N + 1, len(cols))
		for c, name in enumerate(cols):
			t[0, c] = name
	for i, f in enumerate(fish):
		# 体のしなり: 体長Lの後ろ側は L²/(2R) だけ曲がる内側へ。1/R = 旋回速度/速さ
		bend = max(-BEND_MAX, min(BEND_MAX, BEND_GAIN * (BODY * BODY / 2) * (f['w'] / f.get('vnow', f['v']))))
		t[i + 1, 0] = round(f['x'], 4)
		t[i + 1, 1] = round(f['z'], 4)
		t[i + 1, 2] = round(math.degrees(f['th']), 2)
		t[i + 1, 3] = round(bend, 4)
		t[i + 1, 4] = f['kind']
	return
