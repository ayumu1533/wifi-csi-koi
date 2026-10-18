# ステップ2: 頂点シェーダーで鯉を泳がせる（Textportから実行する組み立てスクリプト）
#
# 実行方法（TDのTextportに1行貼る）:
#   exec(open(project.folder + '/scripts/step2_swim.py', encoding='utf-8').read())
#
# 作るもの（ルート / に追加。何度実行しても作り直すだけ）:
#   koi_swim_vert / koi_swim_frag … シェーダーファイルを読むText DAT（ファイル保存で自動反映）
#   koi_swim_mat                   … GLSL MAT（泳ぎの頂点シェーダー＋テクスチャ色）
#   render_top の Override Material を koi_swim_mat に切り替える（koi_mat は比較用に残す）

root = op('/')
folder = project.folder            # このtoeがあるフォルダ（touchdesigner/）
missing = []                       # 見つからなかったパラメーター名（あれば最後に表示）

def setp(o, name, value=None, expr=None):
	# パラメーターを安全に設定する（名前違いは missing に記録）
	p = getattr(o.par, name, None)
	if p is None:
		missing.append(o.name + '.' + name)
	elif expr is not None:
		p.expr = expr
	else:
		p.val = value

# 1) 前回作ったものがあれば消して作り直す
for n in ['koi_swim_vert', 'koi_swim_frag', 'koi_swim_mat', 'koi_swim_info']:
	if root.op(n):
		root.op(n).destroy()

base = op('/koi_static')
x, y = base.nodeX, base.nodeY

# 2) シェーダーファイルを読むText DAT（外部ファイルと同期）
vert = root.create(textDAT, 'koi_swim_vert')
setp(vert, 'file', folder + '/shaders/koi_swim_vert.glsl')
setp(vert, 'syncfile', True)
vert.nodeX, vert.nodeY = x - 200, y - 400

frag = root.create(textDAT, 'koi_swim_frag')
setp(frag, 'file', folder + '/shaders/koi_swim_frag.glsl')
setp(frag, 'syncfile', True)
frag.nodeX, frag.nodeY = x, y - 400

# 3) GLSL MAT
mat = root.create(glslMAT, 'koi_swim_mat')
mat.nodeX, mat.nodeY = x + 250, y - 200
setp(mat, 'vdat', vert.path)
setp(mat, 'pdat', frag.path)

# テクスチャ（FBXから取り出した koi_albedo.png）
setp(mat, 'sampler0name', 'sColorMap')
setp(mat, 'sampler0top', '/koi_static/touchTextures/null_koi_albedo_png')

# UV: POPの頂点属性「Tex」を宣言（シェーダーでは TDTexAttrib_Tex() で読む）
setp(mat, 'attr0name', 'Tex')
setp(mat, 'attr0type', 'float3')

# uniform: 時間
setp(mat, 'vec0name', 'uTime')
setp(mat, 'vec0valuex', expr='absTime.seconds')

# uniform: 泳ぎ方 x=尾の振れ幅[m] y=尾の振り[Hz] z=体に乗る波の数 w=明るさ
setp(mat, 'vec1name', 'uSwim')
setp(mat, 'vec1valuex', 0.022)
setp(mat, 'vec1valuey', 1.2)
setp(mat, 'vec1valuez', 0.6)
setp(mat, 'vec1valuew', 1.0)

# uniform: 形 x=位相 y=曲がり z=体長の半分[m] w=頭の振れの割合
setp(mat, 'vec2name', 'uShape')
setp(mat, 'vec2valuex', 0.0)
setp(mat, 'vec2valuey', 0.0)
setp(mat, 'vec2valuez', 0.25)
setp(mat, 'vec2valuew', 0.03)

# uniform: 確認用（0で通常表示）
setp(mat, 'vec3name', 'uDebug')
for c in 'xyzw':
	setp(mat, 'vec3value' + c, 0)   # 未入力だと1になり確認用モードが有効になってしまうので0にする

# コンパイル結果を見るInfo DAT
info = root.create(infoDAT, 'koi_swim_info')
setp(info, 'op', mat.path)
info.nodeX, info.nodeY = x + 450, y - 200

# 4) 描画をGLSL MATに切り替え
rt = op('/render_top')
setp(rt, 'overridemat', mat.path)

# 5) 確認: すぐ表示できるものを表示し、描画の保存はコンパイルを待つため数フレーム後に行う
print('=== step2 result ===')
print('missing params:', missing)
run("""
m = op('/koi_swim_mat'); rt = op('/render_top')
rt.save(project.folder + '/render_swim.png')
log = op('/koi_swim_info').text.strip()
print('compile log :', log[:1500] if log else 'empty')
print('mat errors  :', m.errors(recurse=True) or 'none')
print('mat warnings:', m.warnings(recurse=True) or 'none')
print('saved render_swim.png')
print('=== step2 done ===')
""", delayFrames=30)
