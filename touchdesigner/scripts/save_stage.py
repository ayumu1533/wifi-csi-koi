# 段階の保存: 今の状態を stages/<名前>/ に、シェーダー・スクリプトのコピーごと残す
#   STAGE = 'stage1_lighting'; exec(open(project.folder + '/scripts/save_stage.py', encoding='utf-8').read())
#
# なぜコピーするか: シェーダーや動きのスクリプトは外部ファイルと同期しているため、
# toe だけを残しても、あとでファイルを書き換えると古い段階の見た目まで変わってしまう。
# 手順:
#   1) ルートにある「ファイル同期しているDAT」の元ファイルを stages/<名前>/ にコピー
#   2) それらのDATの参照先をコピーに付け替えて、stages/<名前>/koi_<名前>.toe として保存
#   3) 参照先を元に戻し、作業用の dev_koi_projection.toe として保存し直す（作業はこちらで続ける）
import os
import shutil

stage = globals().get('STAGE', 'stage')
base = os.path.normpath(project.folder)          # touchdesigner/
dst = os.path.join(base, 'stages', stage)
os.makedirs(dst, exist_ok=True)

orig = {}
for o in op('/').children:
	p = getattr(o.par, 'file', None)
	if o.isDAT and p is not None and p.eval():
		src = os.path.normpath(p.eval())
		if src.startswith(base) and os.path.exists(src):
			rel = os.path.relpath(src, base)
			new = os.path.join(dst, rel)
			os.makedirs(os.path.dirname(new), exist_ok=True)
			shutil.copy2(src, new)
			orig[o.path] = p.eval()
			p.val = new.replace('\\', '/')

project.save(os.path.join(dst, 'koi_' + stage + '.toe').replace('\\', '/'))
saved_stage = project.name

for path, f in orig.items():
	op(path).par.file = f
project.save(os.path.join(base, 'dev_koi_projection.toe').replace('\\', '/'))

print('=== saved stage ===', stage)
print('stage file :', os.path.join(dst, saved_stage))
print('copied     :', sorted(os.path.relpath(f, base) for f in orig.values()))
print('working    :', project.folder, project.name)
