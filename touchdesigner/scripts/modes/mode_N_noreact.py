# 対照条件N「反応なし」: 鯉は泳ぐが、入浴者には反応しない（非インタラクティブ条件）
# TDでは Text DAT「mode_N_noreact」がこのファイルと同期し、koi_school_exec.py から module として呼ばれる。
# 見た目（鯉・泳ぎ・群れ）は案A〜Cと同じで、「反応の有無」だけが違う比較条件にするため。

LABEL = '反応なし（対照）'
REACTS = False   # 人がいても「いない」ときと同じ自由遊泳にする


def respond(f, tgt, pres, env):
	return 0.0, 1.0
