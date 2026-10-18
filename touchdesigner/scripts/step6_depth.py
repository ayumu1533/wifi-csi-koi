# ステップ6②: 鯉ごとに泳ぐ深さを変える（Textportから実行。何度実行してもよい）
#   exec(open(project.folder + '/scripts/step6_depth.py', encoding='utf-8').read())
#
# 動き（koi_school_exec.py）が koi_school に depth（深さ 0〜1）, ty（高さ）, sc（大きさ）を書く。ここでは:
#   koi_school_geo のインスタンス: 高さ ← ty、大きさ ← sc、カスタム属性0の y ← depth
#   koi_swim_mat の uniform:
#     uDepth: x=深いときの暗さ, y=水の色の強さ, z=ツヤを消す割合, w=色あせ
#     uWater: rgb=水の色

geo = op('/koi_school_geo')
mat = op('/koi_swim_mat')
missing = []

def setp(o, name, value):
	p = getattr(o.par, name, None)
	if p is None:
		missing.append(o.name + '.' + name)
	else:
		p.val = value

# 1) インスタンス: 深いほど下に置き（重なったとき浅い鯉が上に描かれる）、少し小さくする
setp(geo, 'instancety', 'ty')
setp(geo, 'instancesx', 'sc')
setp(geo, 'instancesy', 'sc')
setp(geo, 'instancesz', 'sc')
setp(geo, 'instance0customy', 'depth')

# 2) 深さの見え方の uniform
if mat.seq.vec.numBlocks < 8:
	mat.seq.vec.numBlocks = 8

def setvec(i, name, x, y, z, w):
	setattr(mat.par, 'vec%dname' % i, name)
	for c, v in zip('xyzw', (x, y, z, w)):
		setattr(mat.par, 'vec%dvalue%s' % (i, c), v)

setvec(6, 'uDepth', 0.5, 0.7, 0.9, 0.35)
setvec(7, 'uWater', 0.35, 0.75, 0.9, 0.0)

print('=== step6 depth ===')
print('missing params:', missing)

# 3) 確認: コンパイル結果と描画を保存
log = ("open(project.folder + '/depth_log.txt','w').write(op('/koi_swim_info').text + chr(10) + 'warn: ' + str(op('/koi_swim_mat').warnings(recurse=True)) + chr(10) + 'err: ' + str(op('/koi_swim_mat').errors(recurse=True)) + chr(10) + 'geo err: ' + str(op('/koi_school_geo').errors(recurse=True)) + chr(10) + 'cols: ' + ' '.join(op('/koi_school')[0, c].val for c in range(op('/koi_school').numCols)))")
run(log + "; op('/render_top').save(project.folder + '/depth_a.png')", delayFrames=30)
run("op('/render_top').save(project.folder + '/depth_b.png'); print('=== step6 depth done ===')", delayFrames=330)
