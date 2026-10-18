# OSC In DAT（osc_in）のコールバック: 人のいる位置を受け取り、群れのスクリプトに渡す
#
# 受け取るOSC（docs/protocol.md と同じ。送り元は手動テスト tools.osc_manual か本番 localization.estimate）:
#   /koi/zone_probs [p0, p1, ...]  ゾーンごとの確率（zones.json のID昇順, ID0=empty）。約20Hz
#   /koi/zone       [zone_id]      確定ゾーン（変化時＋1秒ごと）
#   /koi/mode       ['A'|'B'|'C'|'N']  応答設計の切り替え（A=寄る B=分散 C=周回 N=反応なし）。手動テスト・スマホのモニター・実験の条件切り替え用
#
# 目標の決め方（TARGET_FROM で切り替える）:
#   'zone'  … 確定ゾーンの中心に寄る。presence = 確定ゾーンが empty なら0、それ以外なら1（既定）
#             推定の確率がばらけていても、確定ゾーン（多数決で決めたもの）へまっすぐ寄る
#   'probs' … presence = 1 - p0、目標位置 = Σ p_i × 中心_i / Σ p_i（ゾーン1以降の確率で重み付け平均）
#             境界で2ゾーンが拮抗すると中間に寄る＝「迷い」の表現。ただし確率がばらけると目標が浴槽の真ん中に寄ってしまう
#             zone_probs が1秒以上来ていないときは、確定ゾーンを確率1として使う
# 座標: zones.json の (x, y) [m] を、TDの (X, Z) にそのまま使う。

import json
import os
import time

TARGET_FROM = 'zone'   # 'zone'（確定ゾーンに寄る）または 'probs'（確率の重み付け平均）

_zones = None
_last_probs = []       # 最後に受け取った zone_probs（'zone' のときも表示用に残す）


def zones():
	# zones.json（server/config/zones.json）を最初の1回だけ読む。IDの昇順に並べた (id, 中心) のリスト
	global _zones
	if _zones is None:
		# toeの場所から上へたどって server/config/zones.json（旧配置の server/zones.json も可）を探す（stages/ の中に保存した段階版でも動くように）
		d = project.folder
		path = None
		for _ in range(5):
			cands = [os.path.join(d, '..', 'server', 'config', 'zones.json'), os.path.join(d, '..', 'server', 'zones.json')]
			cand = next((c for c in cands if os.path.exists(c)), cands[0])
			if os.path.exists(cand):
				path = os.path.normpath(cand)
				break
			d = os.path.dirname(d)
		with open(path, encoding='utf-8') as f:
			data = json.load(f)
		_zones = sorted(((z['id'], z['center']) for z in data['zones']), key=lambda t: t[0])
	return _zones


def publish(probs, source):
	# 確率のリストから presence と目標位置を計算し、群れのスクリプト（koi_school_exec）に渡す
	zs = zones()
	n = min(len(probs), len(zs))
	p_empty = probs[0] if n > 0 else 1.0
	sx = sz = sw = 0.0
	for i in range(n):
		center = zs[i][1]
		if center is None:
			continue
		sx += probs[i] * center[0]
		sz += probs[i] * center[1]
		sw += probs[i]
	inp = {
		't': time.time(),
		'presence': max(0.0, min(1.0, 1.0 - p_empty)),
		'x': sx / sw if sw > 1e-6 else None,
		'z': sz / sw if sw > 1e-6 else None,
		'source': source,
	}
	op('/koi_school_exec').store('input', inp)

	# 見える化用の表（ネットワーク上で今の入力を確認できる）
	st = op('/koi_input_state')
	if st is not None:
		st.clear()
		st.appendRow(['presence', round(inp['presence'], 3)])
		st.appendRow(['target_x', '' if inp['x'] is None else round(inp['x'], 3)])
		st.appendRow(['target_z', '' if inp['z'] is None else round(inp['z'], 3)])
		st.appendRow(['source', source])
		st.appendRow(['target_from', TARGET_FROM])
		st.appendRow(['mode', op('/koi_school_exec').fetch('mode_now', '?') + ('（ファイル名で固定）' if project.name.startswith('exp_') else '')])
		st.appendRow(['probs', ' '.join('%.2f' % p for p in (_last_probs or probs))])


def publish_zone(zid):
	# 確定ゾーンを確率1として渡す（中心に寄る、emptyなら presence 0）
	ids = [z[0] for z in zones()]
	publish([1.0 if i == zid else 0.0 for i in ids], '/koi/zone')


def onReceiveOSC(dat, rowIndex, message, byteData, timeStamp, address, args, peer):
	addr = address.rstrip('/')
	if addr.endswith('/koi/mode'):
		# 応答設計の切り替え。'A'/'B'/'C'/'N' の頭文字だけ見て、Text DAT「koi_mode」を書き換える（小文字でも可）
		m = str(args[0]).strip().upper()[:1] if args else ''
		if project.name.startswith('exp_'):
			print('koi mode: 実験用ファイル（' + project.name + '）なので切り替えません')
		elif m in ('A', 'B', 'C', 'N') and op('/koi_mode') is not None:
			op('/koi_mode').text = m
			print('koi mode ->', m)
		return
	global _last_probs
	if addr.endswith('/koi/zone_probs'):
		_last_probs = [float(a) for a in args]
		op('/koi_school_exec').store('last_probs_time', time.time())
		if TARGET_FROM == 'probs':
			publish(_last_probs, '/koi/zone_probs')
	elif addr.endswith('/koi/zone') and args:
		if TARGET_FROM == 'zone':
			publish_zone(int(args[0]))
		else:
			# 'probs' のときは、zone_probs が1秒以上来ていないときだけ確定ゾーンを使う
			last = op('/koi_school_exec').fetch('last_probs_time', 0)
			if time.time() - last > 1.0:
				publish_zone(int(args[0]))
	return
