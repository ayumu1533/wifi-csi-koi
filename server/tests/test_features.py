"""窓特徴量・周回の割り当て・収集計画のテスト。実行: python -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest

import numpy as np

from csi_receiver.frames import Frame
from collection.plan import build_plan, snake_order, spoken_zone_name
from collection.recording import remove_session
from config.zones import load_zones
from features.stats import WindowFeaturizer, feature_length, frame_to_features
from training.dataset import build_dataset

ZONES = os.path.join(os.path.dirname(__file__), "..", "config", "zones.json")


def make_frame(t: float, values: dict[int, np.ndarray]) -> Frame:
    return Frame(t=t, amplitudes=values)


class WindowFeaturizerTest(unittest.TestCase):
    def test_window_zero_matches_single_frame(self):
        frame = make_frame(0.0, {1: np.arange(1, 65, dtype=float), 2: np.ones(64)})
        f = WindowFeaturizer([1, 2], None, 0)
        np.testing.assert_allclose(f.push(frame), frame_to_features(frame, [1, 2]))

    def test_window_waits_until_full_then_mean_std(self):
        f = WindowFeaturizer([1], None, 3)
        frames = [make_frame(i, {1: np.full(4, v)}) for i, v in enumerate([1.0, 2.0, 3.0])]
        # 平均で正規化するので、一定値の振幅はどれも1になる → 標準偏差0
        self.assertIsNone(f.push(frames[0]))
        self.assertIsNone(f.push(frames[1]))
        feat = f.push(frames[2])
        self.assertEqual(len(feat), feature_length(1, 4, 3))
        np.testing.assert_allclose(feat[:4], 1.0, atol=1e-6)  # 平均
        np.testing.assert_allclose(feat[4:], 0.0, atol=1e-6)  # 標準偏差と揺らぎ

    def test_std_reflects_fluctuation_and_reset(self):
        f = WindowFeaturizer([1], {1: np.ones(2)}, 2)
        f.push(make_frame(0, {1: np.array([1.0, 3.0])}))   # 正規化 [0.5, 1.5]
        feat = f.push(make_frame(1, {1: np.array([3.0, 1.0])}))  # 正規化 [1.5, 0.5]
        np.testing.assert_allclose(feat[:2], [0.0, 0.0], atol=1e-6)  # 平均1 - ベースライン1
        np.testing.assert_allclose(feat[2:4], [0.5, 0.5], atol=1e-6)  # 標準偏差
        np.testing.assert_allclose(feat[4:], [0.5], atol=1e-6)  # 揺らぎの大きさ
        f.reset()
        self.assertIsNone(f.push(make_frame(2, {1: np.ones(2)})))


def make_records(session: str, zone: int, n: int, round_=None) -> list[dict]:
    recs = []
    for i in range(n):
        rec = {"session": session, "zone": zone, "sensor_id": 1, "recv_time": i * 0.05,
               "amplitude": [1.0 + 0.1 * (i % 3)] * 4}
        if round_ is not None:
            rec["round"] = round_
        recs.append(rec)
    return recs


class BuildDatasetTest(unittest.TestCase):
    def setUp(self):
        self.zones = load_zones(ZONES)

    def test_rounds_explicit_or_session_order(self):
        records = (make_records("a", 1, 30) + make_records("b", 2, 30)
                   + make_records("c", 1, 30) + make_records("d", 0, 30, round_=7))
        ds = build_dataset(records, [1], 4, None, 0.05, 0.2, self.zones, window_frames=5)
        rounds = {s: r for s, r in zip(ds.sessions.tolist(), ds.rounds.tolist())}
        self.assertEqual(rounds, {"a": 1, "b": 1, "c": 2, "d": 7})
        self.assertEqual(ds.X.shape[1], feature_length(1, 4, 5))

    def test_stride_thins_frames(self):
        records = make_records("a", 1, 60)
        full = build_dataset(records, [1], 4, None, 0.05, 0.2, self.zones, window_frames=5)
        thin = build_dataset(records, [1], 4, None, 0.05, 0.2, self.zones, window_frames=5, stride=4)
        self.assertEqual(len(thin.y), (len(full.y) + 3) // 4)


class CollectPlanTest(unittest.TestCase):
    def setUp(self):
        self.zones = load_zones(ZONES)

    def test_plan_layout(self):
        steps = build_plan(self.zones, 2, ["立つ", "座る"], 20, 30, 8, 12)
        self.assertEqual(len(steps), 2 + 2 * 10)
        self.assertEqual(steps[0].kind, "baseline")
        self.assertEqual([s.kind for s in steps[1:3]], ["empty", "zone"])
        first = [s.zone_id for s in steps if s.kind == "zone" and s.round == 1]
        second = [s.zone_id for s in steps if s.kind == "zone" and s.round == 2]
        self.assertEqual(first, [1, 2, 3, 6, 5, 4, 7, 8, 9])
        self.assertEqual(second, list(reversed(first)))
        self.assertEqual({s.posture for s in steps if s.round == 2 and s.kind == "zone"}, {"座る"})

    def test_snake_and_names(self):
        self.assertEqual(snake_order(self.zones), [1, 2, 3, 6, 5, 4, 7, 8, 9])
        self.assertEqual(spoken_zone_name("middle_center"), "真ん中の中央")
        self.assertEqual(spoken_zone_name("custom"), "custom")

    def test_remove_session(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"session": "aaa", "v": 1}\n{"session": "bbb", "v": 2}\n{"session": "aaa", "v": 3}\n')
            self.assertEqual(remove_session(path, "aaa"), 2)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"session": "bbb", "v": 2}\n')


if __name__ == "__main__":
    unittest.main()
