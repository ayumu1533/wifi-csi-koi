# ステップ5①: 鯉に陰影とツヤをつける（Textportから実行。何度実行してもよい）
#   exec(open(project.folder + '/scripts/step5_lighting.py', encoding='utf-8').read())
#
# シェーダー（koi_swim_vert/frag.glsl）は保存で自動反映される。ここでは照明のuniformを足すだけ。
#   uLight: xyz=光が来る方向（上が+Y。少し手前左上から）, w=環境光（影の部分の明るさ）
#   uSpec : x=ツヤの強さ, y=ツヤの鋭さ, z=縁の暗さ, w=照明の効き（0=元の平らな見た目, 1=立体）

mat = op('/koi_swim_mat')
if mat.seq.vec.numBlocks < 6:
	mat.seq.vec.numBlocks = 6

def setvec(i, name, x, y, z, w):
	# Vectorsページの i 番目に uniform を設定する
	setattr(mat.par, 'vec%dname' % i, name)
	for c, v in zip('xyzw', (x, y, z, w)):
		setattr(mat.par, 'vec%dvalue%s' % (i, c), v)

setvec(4, 'uLight', -0.4, 1.0, -0.3, 0.55)
setvec(5, 'uSpec', 0.5, 40.0, 0.35, 1.0)

print('=== step5 lighting ===')

# 確認: 照明なし(w=0)・あり(w=1)・法線表示 を順に保存して、最後に「あり」に戻す
log = "open(project.folder + '/light_log.txt','w').write(op('/koi_swim_info').text + chr(10) + 'warn: ' + str(op('/koi_swim_mat').warnings(recurse=True)) + chr(10) + 'err: ' + str(op('/koi_swim_mat').errors(recurse=True)))"
run(log + "; op('/koi_swim_mat').par.vec5valuew = 0", delayFrames=20)
run("op('/render_top').save(project.folder + '/light_off.png'); op('/koi_swim_mat').par.vec5valuew = 1", delayFrames=35)
run("op('/render_top').save(project.folder + '/light_on.png'); op('/koi_swim_mat').par.vec3valuez = 1", delayFrames=50)
run("op('/render_top').save(project.folder + '/light_normal.png'); op('/koi_swim_mat').par.vec3valuez = 0; print('=== step5 lighting done ===')", delayFrames=65)
