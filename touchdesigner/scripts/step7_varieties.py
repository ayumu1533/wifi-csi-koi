# ステップ7: 品種を混ぜる（①の状態から。Textportで実行。何度実行してもよい）
#   exec(open(project.folder + '/scripts/step7_varieties.py', encoding='utf-8').read())
#
# 品種: 0=紅白 1=大正三色 2=黄金 3=丹頂（数は scripts/koi_school_exec.py の KIND_MIX）
# ここでやること:
#   1) ②（深さ）のインスタンス設定を外して①の状態に戻す
#   2) koi_school の kind 列を、インスタンスのカスタム属性0の z としてシェーダーへ渡す
#   3) 品種の見た目の uniform（uKind）を設定
#      uKind: x=大正三色の墨の量(0〜1), y=丹頂の丸の半径[m], z=黄金のツヤの強さ, w=黄金のツヤの鋭さ

geo = op('/koi_school_geo')
mat = op('/koi_swim_mat')
missing = []

def setp(o, name, value):
	p = getattr(o.par, name, None)
	if p is None:
		missing.append(o.name + '.' + name)
	else:
		p.val = value

# 1) ②の設定を外す（高さ・大きさ・深さ）
for name in ['instancety', 'instancesx', 'instancesy', 'instancesz', 'instance0customy']:
	setp(geo, name, '')

# 2) 品種をカスタム属性0の z に
setp(geo, 'instance0customz', 'kind')

# 3) uniform: 6番目を uKind にする（②の uDepth / uWater は使わない）
mat.seq.vec.numBlocks = 7
setattr(mat.par, 'vec6name', 'uKind')
for c, v in zip('xyzw', (0.4, 0.028, 1.6, 18.0)):
	setattr(mat.par, 'vec6value' + c, v)
mat.par.vec3valuew = 0     # 品種の色分け表示はオフ

print('=== step7 varieties ===')
print('missing params:', missing)

# 4) 確認: コンパイル結果、描画、品種の色分け表示、表を保存
log = ("open(project.folder + '/kind_log.txt','w').write(op('/koi_swim_info').text + chr(10) + 'warn: ' + str(op('/koi_swim_mat').warnings(recurse=True)) + chr(10) + 'err: ' + str(op('/koi_swim_mat').errors(recurse=True)) + chr(10) + 'geo err: ' + str(op('/koi_school_geo').errors(recurse=True)) + chr(10) + 'cols: ' + ' '.join(op('/koi_school')[0, c].val for c in range(op('/koi_school').numCols)))")
snap = "t=op('/koi_school'); open(project.folder + '/kind_tab.txt','w').write(chr(10).join(' '.join(t[r,c].val for c in range(t.numCols)) for r in range(1,t.numRows))); op('/render_top').save(project.folder + '/kind_normal.png'); op('/koi_swim_mat').par.vec3valuew = 1"
run(log, delayFrames=20)
run("op('/koi_school_exec').par.active = False; m=op('/koi_swim_mat'); m.par.vec0valuex.expr=''; m.par.vec0valuex=absTime.seconds", delayFrames=25)
run(snap, delayFrames=30)
run("op('/render_top').save(project.folder + '/kind_debug.png'); m=op('/koi_swim_mat'); m.par.vec3valuew = 0; m.par.vec0valuex.expr='absTime.seconds'; op('/koi_school_exec').par.active = True", delayFrames=38)
