# ステップ4: OSCで人の位置を受け取り、鯉を寄せる（Textportから実行する組み立てスクリプト）
#   exec(open(project.folder + '/scripts/step4_osc.py', encoding='utf-8').read())
#
# 作るもの（何度実行しても作り直すだけ）:
#   osc_in            … OSC In DAT。ポート9000で待ち受け（estimate / osc_manual の --osc と同じ）
#   osc_in_callbacks  … OSC In DATのコールバック（scripts/osc_in_callbacks.py と同期）
#   koi_input_state   … 今の入力（人がいる度合い・目標位置・送り元）を表示する表
# 送り元の切り替え（TD側は変更不要。どちらか一方だけ起動する）:
#   手動テスト: server/ で  .venv/Scripts/python.exe -m tools.osc_manual
#   本番      : server/ で  .venv/Scripts/python.exe -m localization.estimate --serial COM3 --model data/model.pkl --osc 127.0.0.1:9000

PORT = 9000

root = op('/')
folder = project.folder
missing = []

def setp(o, name, value=None, expr=None):
	# パラメーターを安全に設定する（名前違いは missing に記録）
	p = getattr(o.par, name, None)
	if p is None:
		missing.append(o.name + '.' + name)
	elif expr is not None:
		p.expr = expr
	else:
		p.val = value

# 1) 作り直し
for n in ['osc_in', 'osc_in_callbacks', 'koi_input_state']:
	if root.op(n):
		root.op(n).destroy()
ex = op('/koi_school_exec')
x, y = ex.nodeX, ex.nodeY

# 2) コールバック（外部ファイルと同期）
cb = root.create(textDAT, 'osc_in_callbacks')
cb.nodeX, cb.nodeY = x - 250, y + 200
setp(cb, 'file', folder + '/scripts/osc_in_callbacks.py')
setp(cb, 'syncfile', True)

# 3) 今の入力を表示する表
st = root.create(tableDAT, 'koi_input_state')
st.nodeX, st.nodeY = x + 250, y + 200
st.clear()
st.appendRow(['presence', 0])

# 4) OSC In DAT
osc = root.create(oscinDAT, 'osc_in')
osc.nodeX, osc.nodeY = x, y + 200
setp(osc, 'port', PORT)
setp(osc, 'callbacks', cb.path)
setp(osc, 'active', True)

# 5) 群れのスクリプトを読み直す（引き寄せの処理を反映）
ex.par.file.pulse() if hasattr(ex.par.file, 'pulse') else None

print('=== step4 osc ===')
print('missing params:', missing)
print('listening on port', PORT)
