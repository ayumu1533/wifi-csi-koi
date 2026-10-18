# ステップ8: 応答設計（案A・B・C＋対照N）を組み込み、案ごとの .toe を保存する（Textportから実行）
#   exec(open(project.folder + '/scripts/step8_conditions.py', encoding='utf-8').read())
#
# 作るもの（何度実行しても作り直すだけ）:
#   mode_A_attract / mode_B_disperse / mode_C_orbit / mode_N_noreact
#        … Text DAT。scripts/modes/ の同名 .py と同期。koi_school_exec が module として呼ぶ
#   koi_mode … Text DAT。今使う案の1文字（A/B/C/N）。OSC /koi/mode でも書き換わる
# 保存するもの（touchdesigner/ に4ファイル。案はファイル名で決まる＝ exp_ の次の1文字）:
#   exp_A_attract.toe / exp_B_disperse.toe / exp_C_orbit.toe / exp_N_noreact.toe
#   最後に koi_mode を A に戻して作業用の dev_koi_projection.toe に保存し直す（作業はこちらで続ける）
# 4ファイルともスクリプト・シェーダーは同じファイルを参照する（＝見た目・群れの動きは常に共通で、反応だけが違う）。

import os

root = op('/')
folder = project.folder.replace('\\', '/')     # touchdesigner/（dev_koi_projection.toe のある場所）
CONDS = [('A', 'mode_A_attract'), ('B', 'mode_B_disperse'), ('C', 'mode_C_orbit'), ('N', 'mode_N_noreact')]
missing = []

def setp(o, name, value):
	# パラメーターを安全に設定する（名前違いは missing に記録）
	p = getattr(o.par, name, None)
	if p is None:
		missing.append(o.name + '.' + name)
	else:
		p.val = value

ex = op('/koi_school_exec')
x, y = ex.nodeX, ex.nodeY

# 1) 案ごとの Text DAT（scripts/modes/*.py と同期）
for i, (key, name) in enumerate(CONDS):
	if root.op(name):
		root.op(name).destroy()
	d = root.create(textDAT, name)
	d.nodeX, d.nodeY = x - 400 + 200 * i, y + 250
	setp(d, 'file', folder + '/scripts/modes/' + name + '.py')
	setp(d, 'syncfile', True)

# 2) 今使う案を入れる Text DAT
if root.op('koi_mode'):
	root.op('koi_mode').destroy()
md = root.create(textDAT, 'koi_mode')
md.nodeX, md.nodeY = x + 400, y + 250
md.text = 'A'

# 3) 確認: 各案のファイルが読めて respond() が動くか（ダミーの鯉1匹で1回呼ぶ）
import math
env = {'TUB': 3.0, 'KEEP': 0.25, 'BOOST': 1.8,
       'steer_to': lambda th, t: (t - th + math.pi) % (2 * math.pi) - math.pi}
fish = {'x': 1.0, 'z': 1.0, 'th': 0.0}
tgt = {'x': 1.5, 'z': 1.5, 'pres': 1.0}
print('=== step8 conditions ===')
for key, name in CONDS:
	d = op('/' + name)
	try:
		m = d.module
		turn, boost = m.respond(fish, tgt, 1.0, env)
		print('%s %-16s lines=%d  label=%s  reacts=%s  respond=(%.3f, %.3f)' % (
			key, name, d.numRows, getattr(m, 'LABEL', '?'), getattr(m, 'REACTS', True), turn, boost))
	except Exception as e:
		print('%s %-16s ERROR: %r' % (key, name, e))

# 4) 案ごとに koi_mode を変えて保存 → 最後に A に戻して作業用ファイルに保存
saved = []
for key, name in CONDS:
	md.text = key
	path = folder + '/exp_' + name[len('mode_'):] + '.toe'   # 例: exp_B_disperse.toe
	project.save(path)
	saved.append(os.path.basename(path) + ' (koi_mode=' + key + ')')
md.text = 'A'
project.save(folder + '/dev_koi_projection.toe')

print('missing params:', missing)
print('saved:', saved)
print('working:', project.folder, project.name)
print('=== step8 done ===')
