// 鯉の色（ピクセルシェーダー）
// 焼き込んだ紅白のテクスチャをもとに、品種ごとに色と模様を変え（品種）、
// 上からの光による陰影と背中の濡れたツヤを足す（立体感）。
// 投影なので「暗い＝光が出ない」。黒は斑点程度にとどめる（黒い面が広いと体に穴が空いたように見える）。
//
// 品種（インスタンスごと。群れのスクリプトの KIND_MIX で数を決める）
//   0 紅白    : テクスチャのまま（白地に赤）
//   1 大正三色: 紅白 ＋ 背中に小さな黒い斑（墨）
//   2 黄金    : 全身が金色。金属のように光る（ツヤを金色・強めに）
//   3 丹頂    : 全身白、頭のてっぺんに赤い丸がひとつ

uniform sampler2D sColorMap;   // koi_albedo.png（紅白）
uniform vec4  uSwim;           // w=明るさ（頂点シェーダーと共通）
uniform vec4  uDebug;          // x>0.5 で白一色, y>0.5 でUVを色で表示, z>0.5 で法線を色で表示, w>0.5 で品種を色分け表示
uniform vec4  uLight;          // xyz=光が来る方向（上が+Y）, w=環境光（影の部分の明るさ 0〜1）
uniform vec4  uSpec;           // x=ツヤの強さ, y=ツヤの鋭さ, z=縁の暗さ(0〜1), w=照明の効き(0=平ら 1=立体)
uniform vec4  uKind;           // x=大正三色の墨の量(0〜1), y=丹頂の丸の半径[m], z=黄金のツヤの強さ, w=黄金のツヤの鋭さ

in Vertex
{
	vec4 color;
	vec3 worldSpacePos;
	vec3 texCoord0;
	vec3 worldNormal;
	vec3 bodyPos;
	flat int kind;
	flat int fishId;
	flat int cameraIndex;
} iVert;

layout(location = 0) out vec4 oFragColor[TD_NUM_COLOR_BUFFERS];

// --- なめらかなノイズ（墨の斑点の形を作る）---
float hash(vec3 p)
{
	p = fract(p * 0.3183099 + 0.1);
	p *= 17.0;
	return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 x)
{
	vec3 i = floor(x);
	vec3 f = fract(x);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
	               mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
	           mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
	               mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
}

void main()
{
	TDCheckDiscard();

	vec3 tex = texture(sColorMap, iVert.texCoord0.st).rgb;
	vec3 bp = iVert.bodyPos;                          // 体の座標（頭が y=-0.25、尾が y=+0.25、背中が z+）

	// ヒレかどうか: テクスチャでヒレは灰色（彩度が低く、白い模様より暗い）
	float vmax = max(tex.r, max(tex.g, tex.b));
	float vmin = min(tex.r, min(tex.g, tex.b));
	float sat  = (vmax - vmin) / max(vmax, 1e-4);
	float fin  = (sat < 0.15 && vmax < 0.8) ? 1.0 : 0.0;

	vec3 base = tex;
	float specGain = 1.0;          // ツヤの強さの倍率
	float specPow  = uSpec.y;      // ツヤの鋭さ
	vec3  specCol  = vec3(1.0);    // ツヤの色（黄金だけ金色）

	if (iVert.kind == 1) {
		// 大正三色: 頭より後ろの背中側に、小さな黒い斑（1匹ごとに位置が変わる）
		float seed = float(iVert.fishId) * 7.31;
		float n = noise(bp * 26.0 + seed) * 0.7 + noise(bp * 70.0 + seed * 1.7) * 0.3;
		float amount = clamp(uKind.x, 0.0, 1.0);
		float th = mix(0.72, 0.45, amount);                // 量が多いほど斑が増える
		float spot = smoothstep(th, th + 0.03, n);
		spot *= smoothstep(-0.13, -0.09, bp.y);            // 頭には入れない
		spot *= smoothstep(-0.03, 0.01, bp.z);             // 背中側だけ
		spot *= 1.0 - fin;
		base = mix(base, vec3(0.05, 0.05, 0.06), spot);
	}
	else if (iVert.kind == 2) {
		// 黄金: 全身を金色に。明るさだけ元の模様を少し残す。ヒレは淡い金色
		float lum = dot(tex, vec3(0.299, 0.587, 0.114));
		vec3 gold = vec3(1.0, 0.76, 0.30);
		base = gold * (0.75 + 0.25 * lum);
		base = mix(base, gold * 0.8 + 0.1, fin);
		specGain = uKind.z;
		specPow  = uKind.w;
		specCol  = vec3(1.0, 0.85, 0.55);
	}
	else if (iVert.kind == 3) {
		// 丹頂: 体は白、ヒレは元の灰色。頭のてっぺんに赤い丸ひとつ
		base = mix(vec3(0.95, 0.94, 0.92), tex, fin);
		float r = max(uKind.y, 0.005);
		float dHead = length(vec2(bp.x, bp.y + 0.16));      // 頭のてっぺん (x=0, y=-0.16) からの距離
		float dot_ = (1.0 - smoothstep(r - 0.004, r, dHead)) * step(0.0, bp.z) * (1.0 - fin);
		base = mix(base, vec3(0.88, 0.16, 0.12), dot_);
	}
	// kind 0（紅白）はテクスチャのまま

	base *= (uSwim.w > 0.0 ? uSwim.w : 1.0);

	// --- 照明（①）---
	vec3 N = normalize(iVert.worldNormal);
	vec3 V = vec3(0.0, 1.0, 0.0);
	if (dot(N, V) < 0.0) N = -N;               // ヒレなど薄い面の裏側も表として扱う
	vec3 L = normalize(length(uLight.xyz) > 0.0 ? uLight.xyz : vec3(-0.4, 1.0, -0.3));

	float ambient = uLight.w;
	float diffuse = max(dot(N, L), 0.0);
	float shade = ambient + (1.0 - ambient) * diffuse;

	float facing = clamp(dot(N, V), 0.0, 1.0);
	float rim = mix(1.0 - uSpec.z, 1.0, sqrt(facing));

	vec3 H = normalize(L + V);
	float spec = uSpec.x * specGain * pow(max(dot(N, H), 0.0), max(specPow, 1.0));

	vec3 lit = base * shade * rim + specCol * spec;
	vec3 col = mix(base, lit, clamp(uSpec.w, 0.0, 1.0));
	vec4 outCol = vec4(col, 1.0);

	// 確認用の表示切り替え
	if (uDebug.x > 0.5) outCol = vec4(1.0);
	if (uDebug.y > 0.5) outCol = vec4(iVert.texCoord0.st, 0.0, 1.0);
	if (uDebug.z > 0.5) outCol = vec4(N * 0.5 + 0.5, 1.0);
	if (uDebug.w > 0.5) {
		vec3 kc[4] = vec3[4](vec3(1,0.2,0.2), vec3(0.2,0.2,0.2), vec3(1,0.8,0.2), vec3(0.9,0.9,0.9));
		outCol = vec4(kc[clamp(iVert.kind, 0, 3)], 1.0);
	}

	TDAlphaTest(outCol.a);
	oFragColor[0] = TDOutputSwizzle(outCol);
	for (int i = 1; i < TD_NUM_COLOR_BUFFERS; i++)
		oFragColor[i] = vec4(0.0);
}
