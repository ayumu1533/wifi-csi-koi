# 鯉モデルをTD用に整える: 動きを止める→テクスチャを焼き直す→ポリゴン削減→縮小→書き出し
# 使い方: blender -b -P prepare_for_td.py -- <入力.blend> <出力フォルダ> [削減率=0.15] [全長m=0.5]
import bpy, math, os, sys
args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
SRC=os.path.abspath(args[0])
OUT=os.path.abspath(args[1]); os.makedirs(OUT, exist_ok=True)
DECIMATE=float(args[2]) if len(args)>2 else 0.15   # 残すポリゴンの割合
TARGET_LEN=float(args[3]) if len(args)>3 else 0.5  # 鯉の全長[m]（ヒゲ先〜尾びれ先）
TEX=2048                                           # テクスチャ解像度
bpy.ops.wm.open_mainfile(filepath=SRC)
s=bpy.context.scene; vl=bpy.context.view_layer
body=bpy.data.objects['body']; arm=bpy.data.objects['KoiArmature']

# 1) 動きを止める: レストポーズにしてアーマチュアを外す
arm.data.pose_position='REST'
for m in list(body.modifiers):
    if m.type=='ARMATURE': body.modifiers.remove(m)
mw=body.matrix_world.copy(); body.parent=None; body.matrix_world=mw
for g in list(body.vertex_groups): body.vertex_groups.remove(g)
for o in [arm, bpy.data.objects.get('Light')]:
    if o: bpy.data.objects.remove(o, do_unlink=True)
for a in list(bpy.data.actions): bpy.data.actions.remove(a)
s.frame_start=s.frame_end=1

# 材質なしスロット(目の白)に材質を割り当てる（焼き込みに必要）
eye=bpy.data.materials.new('KoiEye_White'); eye.diffuse_color=(0.9,0.9,0.9,1)
pb=eye.node_tree.nodes.get('Principled BSDF'); pb.inputs['Base Color'].default_value=(0.9,0.9,0.9,1)
body.data.materials[1]=eye

for o in s.objects: o.select_set(False)
body.select_set(True); vl.objects.active=body

me=body.data
# 2) サブディビジョンLv2を適用（見た目はFBXと同じ）
for m in body.modifiers:
    if m.type=='SUBSURF': m.levels=2; bpy.ops.object.modifier_apply(modifier=m.name)
print('after subsurf faces',len(me.polygons))

# 3) ポリゴン削減（UVは保持される）
d=body.modifiers.new('Decimate','DECIMATE'); d.ratio=DECIMATE
bpy.ops.object.modifier_apply(modifier=d.name)
print('after decimate faces',len(me.polygons),'verts',len(me.vertices))

print('rest_position kept:', 'rest_position' in me.attributes)
# 4) 焼き込み用UV（素材同士が重ならない1枚のUV）を最終メッシュに作る
atlas=me.uv_layers.new(name='AtlasUV'); me.uv_layers.active=atlas
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
bpy.ops.uv.pack_islands(margin=0.01)
bpy.ops.object.mode_set(mode='OBJECT')

# 5) 紅白模様をAtlasUVに焼き込む（rest_positionベースの手続き模様なのでUVに依存しない）
img=bpy.data.images.new('koi_albedo',TEX,TEX,alpha=False); img.generated_color=(0,0,0,1)
for mat in me.materials:
    nt=mat.node_tree
    for n in list(nt.nodes):
        if n.type=='TEX_IMAGE': nt.nodes.remove(n)   # 中身のない古い焼き込みノードを消す
    t=nt.nodes.new('ShaderNodeTexImage'); t.image=img
    uvn=nt.nodes.new('ShaderNodeUVMap'); uvn.uv_map='AtlasUV'; nt.links.new(uvn.outputs[0],t.inputs[0])
    nt.nodes.active=t
s.render.engine='CYCLES'; s.cycles.device='CPU'; s.cycles.samples=8
bk=s.render.bake; bk.use_pass_direct=False; bk.use_pass_indirect=False; bk.use_pass_color=True; bk.margin=16
bpy.ops.object.bake(type='DIFFUSE')
img.filepath_raw=os.path.join(OUT,'koi_albedo.png'); img.file_format='PNG'; img.save()

# 6) 材質を1つにまとめる（焼いた画像1枚を貼るだけ）
koi=bpy.data.materials.new('KoiAtlas')
nt=koi.node_tree; bsdf=nt.nodes['Principled BSDF']
t=nt.nodes.new('ShaderNodeTexImage'); t.image=img; nt.links.new(t.outputs['Color'],bsdf.inputs['Base Color'])
bsdf.inputs['Roughness'].default_value=0.6
me.materials.clear(); me.materials.append(koi)
for p in me.polygons: p.material_index=0
me.uv_layers.remove(me.uv_layers['UVMap']); me.uv_layers['AtlasUV'].name='UVMap'
if 'rest_position' in me.attributes: me.attributes.remove(me.attributes['rest_position'])
for p in me.polygons: p.use_smooth=True

# 7) 原点を中心に・全長TARGET_LENに縮小して適用
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
body.location=(0,0,0)
L=body.dimensions.y; sc=TARGET_LEN/L; body.scale=(sc,sc,sc)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
body.name='koi'; me.name='koi'
print('dims',tuple(round(v,4) for v in body.dimensions),'scale',sc)
for b in [v.co for v in me.vertices]: pass
ys=[v.co.y for v in me.vertices]; print('blender y range',min(ys),max(ys))

# 8) 書き出し（TD座標: Y-up。Blenderの-Y(頭)→TDの+Z）
# FBXは前回TDで読めた書き出し設定（単位メタデータ=m）に合わせる。OBJは単位なし＝1単位1m
bpy.ops.wm.obj_export(filepath=os.path.join(OUT,'koi_static.obj'), export_selected_objects=True,
    forward_axis='NEGATIVE_Z', up_axis='Y', export_materials=True, path_mode='RELATIVE', export_uv=True, export_normals=True, apply_modifiers=True)
bpy.ops.export_scene.fbx(filepath=os.path.join(OUT,'koi_static.fbx'), use_selection=True, object_types={'MESH'},
    axis_forward='-Z', axis_up='Y', bake_space_transform=False, apply_scale_options='FBX_SCALE_UNITS', path_mode='COPY', embed_textures=True, bake_anim=False, add_leaf_bones=False)
img.pack()
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT,'koi_td_ready.blend'), compress=True)
