"""収集済みJSONL → 学習・評価用のデータセット（特徴量X・ゾーンラベルy・グループ）。

train と learning_curve で共用する。
  1. JSONLを読み、学習に使うセンサーのレコードだけに絞る（load_training_data）
  2. --baseline の先頭セッションからベースラインを求め、残りをゾーン0の学習データにする（load_baseline）
  3. セッションごとにFrameAssemblerでフレームを再構成し、特徴量にする（build_dataset。推論時と同一の処理）。
     特徴量の種類は input_kind で選ぶ（"stats" = 統計特徴量、"window" = CNN用の生の窓。features/__init__.py）
"""
from __future__ import annotations

import argparse
import glob
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from common.jsonl import load_jsonl
from config.zones import DEFAULT_ZONES_PATH, ZoneConfig
from csi_receiver.frames import FrameAssembler
from features import make_featurizer
from features.amplitude import compute_baseline
from models.registry import ALGOS

logger = logging.getLogger("dataset")

DEFAULT_INTERVAL_S = 0.05
DEFAULT_MAX_AGE_S = 0.2
DEFAULT_WINDOW_FRAMES = 20  # 0.05s x 20 = 約1秒


@dataclass
class Dataset:
    X: np.ndarray         # 特徴量（フレーム数, 特徴量次元）
    y: np.ndarray         # ゾーンID
    sessions: np.ndarray  # セッションID（評価のグループ分け用）
    dates: np.ndarray     # 収集日（旧データはNone）
    rounds: np.ndarray    # 周回番号（collection.planの"round"。無ければゾーンごとのセッション順）

    def groups(self, split: str) -> np.ndarray:
        return {"session": self.sessions, "date": self.dates, "round": self.rounds}[split]


def zone_label(zone_config: ZoneConfig, zone_id: int) -> str:
    """zones.jsonに定義がないIDでも表示だけは落ちないようにする。"""
    try:
        return zone_config.by_id(zone_id).name
    except KeyError:
        return "?"


def build_dataset(
    records: list[dict],
    sensor_ids: list[int],
    expected_len: int,
    baseline: dict[int, np.ndarray] | None,
    interval_s: float,
    max_age_s: float,
    zone_config: ZoneConfig,
    window_frames: int = 0,
    stride: int = 1,
    input_kind: str = "stats",
) -> Dataset:
    """セッションごとにフレーム同期を再生し、特徴量X・ゾーンラベルyを作る。

    あわせて、セッション単位分割(B1)に使うsession_id配列、
    収集日単位分割に使うdate配列（"date"フィールドが無い旧データはNone）、
    周回単位分割・学習曲線に使うround配列も返す。
    "round"フィールドが無いセッションは、同じゾーンの中で何番目のセッションか（ファイル順、1始まり）を周回とみなす。

    stride > 1 なら特徴量をstrideフレームおきに間引く（窓特徴量は隣どうしがほぼ同じなので、
    学習・評価を速くするため）。

    input_kind="window" のときは X が (サンプル数, 窓フレーム数, センサー数, サブキャリア数) の
    float32 になる（メモリを抑えるため）。

    レコードに "zone" フィールドがあればそれを使い、
    無ければ旧形式（"x"/"y"座標付き）とみなし zones.json の矩形から変換する。
    """
    sessions: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        sessions[rec["session"]].append(rec)

    features = []
    labels = []
    session_arr = []
    date_arr = []
    round_arr = []
    sessions_per_zone: Counter[int] = Counter()
    for session_id, recs in sessions.items():
        recs.sort(key=lambda r: r["recv_time"])
        first = recs[0]
        if "zone" in first:
            zone_id = first["zone"]
        else:
            zone_id = zone_config.point_to_zone(first["x"], first["y"])
            if zone_id is None:
                logger.warning(
                    "session %s: coordinates (%.2f, %.2f) match no zone rect in zones.json - skipped",
                    session_id, first["x"], first["y"],
                )
                continue
        session_date = first.get("date")  # 旧データにはフィールド自体が無い
        sessions_per_zone[zone_id] += 1
        session_round = first.get("round", sessions_per_zone[zone_id])

        assembler = FrameAssembler(
            sensor_ids, interval_s=interval_s, max_age_s=max_age_s, expected_len=expected_len
        )
        featurizer = make_featurizer(input_kind, sensor_ids, baseline, window_frames)
        n_frames = 0
        n_windows = 0
        for rec in recs:
            frame = assembler.add(rec["sensor_id"], rec["recv_time"], np.asarray(rec["amplitude"]))
            if frame is None:
                continue
            feat = featurizer.push(frame)
            if feat is None:
                continue
            n_windows += 1
            if (n_windows - 1) % stride != 0:
                continue
            features.append(feat)
            labels.append(zone_id)
            session_arr.append(session_id)
            date_arr.append(session_date)
            round_arr.append(session_round)
            n_frames += 1
        present = {r["sensor_id"] for r in recs}
        missing = set(sensor_ids) - present
        if missing:
            logger.warning("session %s (zone %d): sensors %s missing -> 0 frames",
                           session_id, zone_id, sorted(missing))
        else:
            logger.info("session %s (date=%s round=%s): zone %d (%s): %d frames",
                        session_id, session_date, session_round, zone_id,
                        zone_label(zone_config, zone_id), n_frames)

    if not features:
        raise SystemExit("no frames could be assembled - check sensor coverage in the data")
    return Dataset(
        X=np.asarray(features, dtype=np.float32) if input_kind == "window" else np.asarray(features),
        y=np.asarray(labels),
        sessions=np.asarray(session_arr, dtype=object),
        dates=np.asarray(date_arr, dtype=object),
        rounds=np.asarray(round_arr, dtype=object),
    )


def load_baseline(path: str, sensor_ids: list[int]) -> tuple[dict[int, np.ndarray], list[dict]]:
    """--baseline のJSONLから (1)特徴量正規化用のベースライン振幅パターン と
    (2)ゾーン0（empty）の学習データとして追加できるレコード を作る。

    ベースライン算出に使ったセッションをそのまま学習にも使うと評価が甘くなるため、
    先頭セッションはベースライン算出専用にし、残りのセッションだけをゾーン0の
    学習データとして返す（セッションが1つしかない場合は学習データには使わない）。
    """
    baseline_records = load_jsonl(path)
    sessions: dict[str, list[dict]] = defaultdict(list)
    for rec in baseline_records:
        sessions[rec.get("session", "unknown")].append(rec)
    session_ids = list(sessions)  # ファイル内の記録順（先頭 = 最初に収集したセッション）

    calc_session = session_ids[0]
    baseline = compute_baseline(sessions[calc_session], sensor_ids)
    logger.info("baseline pattern computed from session %s (%d records)",
                calc_session, len(sessions[calc_session]))

    train_sessions = session_ids[1:]
    if not train_sessions:
        logger.info(
            "only 1 baseline session (%s) - used for the baseline pattern only, not as zone-0 training data "
            "(reusing the same session for baseline calc and training would leak evaluation). "
            "zone-0 training data comes from '--zone 0' sessions in --data (collection.plan does this).",
            calc_session,
        )
        return baseline, []

    zero_records = [rec for sid in train_sessions for rec in sessions[sid]]
    for rec in zero_records:
        rec.setdefault("zone", 0)
    logger.info("zone-0 training sessions=%s (%d records)", train_sessions, len(zero_records))
    return baseline, zero_records


@dataclass
class TrainingData:
    records: list[dict]
    paths: list[str]
    sensor_ids: list[int]
    expected_len: int
    baseline: dict[int, np.ndarray] | None


def load_training_data(data_patterns: list[str], baseline_path: str | None,
                       zone_config: ZoneConfig, sensors_override: str | None) -> TrainingData:
    """--data / --baseline のJSONLを読み、学習に使うレコード・センサー・ベースラインを決める。"""
    paths = []
    for pattern in data_patterns:
        matched = glob.glob(pattern)
        paths.extend(matched if matched else [pattern])
    records = []
    for path in paths:
        recs = load_jsonl(path)
        logger.info("loaded %d records from %s", len(recs), path)
        records.extend(recs)
    if not records:
        raise SystemExit("no records loaded")

    sensor_ids = zone_config.resolve_sensor_ids(sensors_override)
    present = {rec["sensor_id"] for rec in records}
    absent = [sid for sid in sensor_ids if sid not in present]
    if absent:
        raise SystemExit(f"収集データに使用センサー {absent} のレコードがありません"
                         f"（データ内のセンサー: {sorted(present)}）。--sensors か zones.json の enabled を確認してください")
    records = [rec for rec in records if rec["sensor_id"] in sensor_ids]
    length_counts = Counter(len(rec["amplitude"]) for rec in records)
    expected_len = length_counts.most_common(1)[0][0]
    logger.info("sensors: %s, subcarriers per sensor: %d (lengths seen: %s)",
                sensor_ids, expected_len, dict(length_counts))

    baseline = None
    if baseline_path:
        baseline, zero_records = load_baseline(baseline_path, sensor_ids)
        records.extend(zero_records)
    return TrainingData(records, paths, sensor_ids, expected_len, baseline)


def add_dataset_args(parser: argparse.ArgumentParser) -> None:
    """train と learning_curve で共通の引数。"""
    parser.add_argument("--data", nargs="+", required=True, help="collection で作ったJSONL（glob可）")
    parser.add_argument("--baseline", default=None,
                        help="空浴槽(zone 0)のCSI記録JSONL（任意。先頭セッションを特徴量の基準に使い、"
                             "残りのセッションはzone-0の学習データにする）")
    parser.add_argument("--zones", default=DEFAULT_ZONES_PATH, help="ゾーン定義ファイル")
    parser.add_argument("--sensors", default=None,
                        help="学習に使うセンサーID（例: 1,3）。省略時はzones.jsonのenabledに従う")
    parser.add_argument("--algo", choices=ALGOS, default="et",
                        help="et（ExtraTrees、標準）/ rf / knn（従来の機械学習）、mlp / cnn1d / cnn2d（深層学習。PyTorchが必要）")
    parser.add_argument("--seed", type=int, default=42, help="乱数シード（RandomForestのrandom_state、深層学習の初期値）")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="フレーム周期（秒）")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_S, help="サンプル鮮度の許容秒数")


def add_deep_args(parser: argparse.ArgumentParser) -> None:
    """深層学習（--algo mlp / cnn1d / cnn2d）の学習設定。既定値は models/deep/trainer.py の TrainConfig。"""
    group = parser.add_argument_group("深層学習（mlp / cnn1d / cnn2d）")
    group.add_argument("--max-epochs", type=int, default=60, help="早期終了で使うエポック数の上限")
    group.add_argument("--patience", type=int, default=10, help="検証macro-F1がこのエポック数伸びなければ止める")
    group.add_argument("--batch-size", type=int, default=128)
    group.add_argument("--lr", type=float, default=1e-3, help="学習率（AdamW）")
    group.add_argument("--weight-decay", type=float, default=1e-3)
    group.add_argument("--dropout", type=float, default=0.3)
    group.add_argument("--noise-std", type=float, default=0.1, help="データ拡張のノイズ（標準化後の単位、0で無効）")
    group.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto",
                       help="学習に使う装置。auto はGPU（CUDA）があればGPU（Google Colab用）、無ければCPU")


def train_config_from_args(args: argparse.Namespace):
    """add_deep_args の値から TrainConfig を作る（深層学習のときだけ。PyTorchを読み込む）。"""
    from models.deep import TrainConfig
    return TrainConfig(max_epochs=args.max_epochs, patience=args.patience, batch_size=args.batch_size,
                       lr=args.lr, weight_decay=args.weight_decay, dropout=args.dropout,
                       noise_std=args.noise_std, device=args.device)
