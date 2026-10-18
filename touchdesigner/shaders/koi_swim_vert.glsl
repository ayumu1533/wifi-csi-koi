// 鯉の泳ぎ（頂点シェーダー）
// 止まった鯉のメッシュを、体の前後方向に沿った波で左右にくねらせる。
//
// 座標の前提（koi_static.fbx をFBX COMPで読んだときのオブジェクト空間）:
//   Y = 体の前後方向（頭が -Y, 尾が +Y）、X = 左右、Z = 背中側（Blenderの座標のまま）
//   曲げたあと、このシェーダーの中で X軸まわりに -90° 回して TD の Y-up（頭が +Z）に直す。
//   群れ（インスタンス）は koi_school_geo が描く。1匹ごとの位置・向きはインスタンスの変換、
//   1匹ごとの体の曲がり・品種はインスタンスのカスタム属性0の x・z（TDInstanceCustomAttrib0()）で受け取る。
//   POPなので頂点属性は関数で読む: 位置=TDPos(), 法線=TDNormal(), UV=TDTexAttrib_Tex()（Attributesページで Tex を宣言）。
//   変形はTDDeform（＝COMPの位置・回転を掛ける）より前に、オブジェクト空間で行う。

uniform float uTime;     // 経過時間 [秒]
uniform vec4  uSwim;     // x=尾の振れ幅[m], y=尾の振り[Hz], z=体に乗る波の数, w=明るさ
uniform vec4  uShape;    // x=位相オフセット, y=全員に足す曲がり（通常0）, z=体長の半分[m], w=頭の振れの割合

out Vertex
{
	vec4 color;
	vec3 worldSpacePos;
	vec3 texCoord0;
	vec3 worldNormal;      // 照明用: 曲げたあとの面の向き（ワールド空間）
	vec3 bodyPos;          // 品種の模様用: 曲げる前の体の座標（Blenderの座標: y=前後, x=左右, z=上下）
	flat int kind;         // 品種（0=紅白 1=大正三色 2=黄金 3=丹頂）
	flat int fishId;       // 1匹ごとの番号（模様を1匹ずつ変えるため）
	flat int cameraIndex;
} oVert;

void main()
{
	// UVはそのまま渡す（頂点と一緒に動くので模様は滑らない）
	// ※ POPでは属性を変数(P, uv)ではなく関数で読む。
	//   UVはPOPの頂点属性「Tex」。GLSL MATのAttributesページで Tex を宣言して TDTexAttrib_Tex() で読む
	oVert.texCoord0.stp = TDTexAttrib_Tex(0);

	vec3 p = TDPos();
	oVert.bodyPos = p;     // 曲げる前の位置を模様用に渡す（泳いでも模様が体に張り付いたまま）

	// s: 頭=0 〜 尾=1 の体の位置
	// uniformが未設定(0)でも壊れないように最小値を設ける（0で割ると頂点がNaNになり何も描かれない）
	float halfLen = max(uShape.z, 0.001);
	float s = clamp((p.y + halfLen) / (2.0 * halfLen), 0.0, 1.0);

	// 1匹ごとに位相をずらす（インスタンス番号×黄金比で偏りなく散らす）
	float phase = uShape.x + float(TDInstanceID()) * 1.6180339;

	// 振れ幅の分布: 頭はほとんど振れず、尾に向かって大きくなる（コイ科の泳ぎ方）
	float envelope = uShape.w + (1.0 - uShape.w) * s * s;

	// 頭から尾へ進む波: sin(2π(波数×s − 振動数×時間) + 位相)
	float wave = sin(6.2831853 * (uSwim.z * s - uSwim.y * uTime) + phase);

	// 1匹ごとの体の曲がりと品種（群れのスクリプトが計算した値。カスタム属性0の x と z）
	vec4 custom = TDInstanceCustomAttrib0();
	float bend = custom.x + uShape.y;
	oVert.kind = int(custom.z + 0.5);
	oVert.fishId = TDInstanceID();

	// 左右(X)へのずれ = 波 + 旋回時の体の曲がり
	p.x += uSwim.x * envelope * wave;
	p.x += bend * s * s;

	// --- 法線（面の向き）も曲げに合わせて直す ---
	// 体は x' = x + f(y) と曲がっている。このとき法線は n' = (nx, ny - f'(y)·nx, nz) になる。
	// f'(y) = df/ds × ds/dy、ds/dy = 1/(2×体長の半分)
	float dEnv  = 2.0 * (1.0 - uShape.w) * s;                                        // 振れ幅の分布の傾き
	float dWave = 6.2831853 * uSwim.z * cos(6.2831853 * (uSwim.z * s - uSwim.y * uTime) + phase); // 波の傾き
	float dfds  = uSwim.x * (dEnv * wave + envelope * dWave) + 2.0 * bend * s;
	float dfdy  = dfds / (2.0 * halfLen);
	vec3 n = TDNormal();
	n = normalize(vec3(n.x, n.y - dfdy * n.x, n.z));

	// Blenderの座標(Z-up) → TDの座標(Y-up): X軸まわりに-90°（頭が +Z、背中が +Y になる）
	vec3 q = vec3(p.x, p.z, -p.y);
	vec3 nq = vec3(n.x, n.z, -n.y);   // 法線も同じ回転

	// TDDeformでインスタンスの変換（1匹ごとの位置・向き）とCOMPの変換を掛けてワールド空間へ
	vec4 worldSpacePos = TDDeform(q);
	vec3 uvUnwrapCoord = TDInstanceTexCoord(TDUVUnwrapCoord());
	gl_Position = TDWorldToProj(worldSpacePos, uvUnwrapCoord);

#ifndef TD_PICKING_ACTIVE
	oVert.cameraIndex = TDCameraIndex();
	oVert.worldSpacePos.xyz = worldSpacePos.xyz;
	oVert.worldNormal = normalize(TDDeformNorm(nq));   // インスタンスの向きも反映したワールド空間の法線
	oVert.color = vec4(1.0);   // 頂点カラーは使わない
#else
	TDWritePickingValues();
#endif
}
