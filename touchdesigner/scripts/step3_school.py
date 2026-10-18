# ステップ3b: 鯉を群れ（N匹）にする（Textportから実行する組み立てスクリプト）
#   exec(open(project.folder + '/scripts/step3_school.py', encoding='utf-8').read())
#
# 作るもの（何度実行しても作り直すだけ）:
#   koi_school      … Table DAT。1匹1行で tx, tz, ry, bend が入る
#   koi_school_exec … Execute DAT。毎フレーム scripts/koi_school_exec.py を実行して koi_school を更新
#   koi_school_geo  … Geometry COMP。koi_static のメッシュを koi_school の行数だけインスタンスで描く
# 変えるもの:
#   render_top は koi_school_geo だけを描く（1匹版の koi_static は描かない＝メッシュの読み込み元として残す）
#   1匹版の koi_motion_exec は止める

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
for n in ['koi_school', 'koi_school_exec', 'koi_school_geo']:
	if root.op(n):
		root.op(n).destroy()
base = op('/koi_static')
x, y = base.nodeX, base.nodeY

# 2) 群れの状態を入れる Table DAT（中身はExecute DATが毎フレーム書く）
tab = root.create(tableDAT, 'koi_school')
tab.nodeX, tab.nodeY = x - 500, y + 200
tab.setSize(2, 4)
for c, name in enumerate(['tx', 'tz', 'ry', 'bend']):
	tab[0, c] = name
tab[1, 0], tab[1, 1], tab[1, 2], tab[1, 3] = 1.5, 1.5, 0, 0

# 3) 毎フレーム群れを動かす Execute DAT（外部ファイルと同期）
ex = root.create(executeDAT, 'koi_school_exec')
ex.nodeX, ex.nodeY = x - 500, y + 400
setp(ex, 'file', folder + '/scripts/koi_school_exec.py')
setp(ex, 'syncfile', True)
setp(ex, 'framestart', True)
setp(ex, 'active', True)

# 4) 群れを描く Geometry COMP（中に koi_static のメッシュを参照する Select POP）
geo = root.create(geometryCOMP, 'koi_school_geo')
geo.nodeX, geo.nodeY = x, y - 250
for c in list(geo.children):
	c.destroy()
sel = geo.create(selectPOP, 'mesh')
setp(sel, 'pop', '/koi_static/koi/mesh')
sel.render = True
sel.display = True
setp(geo, 'material', '/koi_swim_mat')

# インスタンス: koi_school の行 = 1匹。位置(tx, tz)・向き(ry)と、カスタム属性0に曲がり(bend)
setp(geo, 'instancing', True)
setp(geo, 'instanceop', tab.path)
setp(geo, 'instancefirstrow', 'names')
setp(geo, 'instancetx', 'tx')
setp(geo, 'instancetz', 'tz')
setp(geo, 'instancery', 'ry')
geo.seq.instance.numBlocks = 1
setp(geo, 'instance0customop', tab.path)
setp(geo, 'instance0customx', 'bend')

# 5) 描画対象を群れに切り替え、1匹版を止める
setp(op('/render_top'), 'geometry', 'koi_school_geo')
mat = op('/koi_swim_mat')
mat.par.vec2valuey.expr = ''
mat.par.vec2valuey = 0          # 全員に足す曲がりは0（1匹ごとの曲がりはインスタンスから来る）
if op('/koi_motion_exec'):
	op('/koi_motion_exec').par.active = False

print('=== step3 school ===')
print('missing params:', missing)

# 6) 確認: 数フレーム後と5秒後に描画を保存、コンパイル結果を記録
def check(tag):
	return ("open(project.folder + '/school_log.txt','a',encoding='utf-8').write('%s rows=' + str(op('/koi_school').numRows) + ' instances=' + str(op('/koi_school_geo').numInstances if hasattr(op('/koi_school_geo'),'numInstances') else '?') + ' log=' + op('/koi_swim_info').text.replace(chr(10),' ')[:400] + ' warn=' + str(op('/koi_swim_mat').warnings(recurse=True)) + ' err=' + str(op('/koi_school_geo').errors(recurse=True)) + chr(10)); op('/render_top').save(project.folder + '/school_%s.png')" % (tag, tag))
open(folder + '/school_log.txt', 'w', encoding='utf-8').write('')
run(check('a'), delayFrames=30)
run(check('b') + "; print('=== step3 school done ===')", delayFrames=330)
