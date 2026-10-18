"""モデルの共通の形（models/）と生の窓（features/tensor.py）のテスト。実行: python -m unittest discover -s tests -t ."""
import os
import pickle
import tempfile
import unittest

import numpy as np

from csi_receiver.frames import Frame
from features import input_shape, make_featurizer
from features.tensor import WindowTensorizer
from models.classic import ClassicModel, make_pipeline
from models.registry import INPUT_KIND, create_model, load_bundle

try:
    import torch  # noqa: F401
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def toy_stats(n_per_class=40, n_features=12, seed=0):
    """クラスごとに平均がずれた簡単なデータ（3クラス、2グループ）。"""
    rng = np.random.default_rng(seed)
    X, y, g = [], [], []
    for c in range(3):
        X.append(rng.normal(loc=c * 2.0, size=(n_per_class, n_features)))
        y += [c] * n_per_class
        g += [1] * (n_per_class // 2) + [2] * (n_per_class - n_per_class // 2)
    return np.vstack(X), np.array(y), np.array(g)


class TensorizerTest(unittest.TestCase):
    def test_shape_and_baseline(self):
        sensors = [1, 3]
        baseline = {1: np.ones(4), 3: np.zeros(4)}
        tz = WindowTensorizer(sensors, baseline, window_frames=3)
        out = None
        for t in range(3):
            frame = Frame(t=t, amplitudes={1: np.full(4, 2.0), 3: np.arange(1, 5, dtype=float)})
            out = tz.push(frame)
            if t < 2:
                self.assertIsNone(out)  # 窓がたまるまではNone
        self.assertEqual(out.shape, (3, 2, 4))
        self.assertEqual(out.dtype, np.float32)
        np.testing.assert_allclose(out[0, 0], np.zeros(4), atol=1e-6)  # 正規化(=1) − ベースライン(1)
        np.testing.assert_allclose(out[0, 1], np.arange(1, 5) / 2.5, atol=1e-6)

    def test_input_shape(self):
        self.assertEqual(input_shape("stats", 3, 64, 20), (387,))
        self.assertEqual(input_shape("window", 3, 64, 40), (40, 3, 64))
        self.assertEqual(make_featurizer("window", [1], None, 5).__class__, WindowTensorizer)


class ClassicModelTest(unittest.TestCase):
    def test_same_as_pipeline(self):
        X, y, g = toy_stats()
        model = ClassicModel("rf", seed=42)
        model.fit(X, y, g)
        pipe = make_pipeline("rf", 42)
        pipe.fit(X, y)
        np.testing.assert_array_equal(model.predict_proba(X), pipe.predict_proba(X))
        self.assertEqual(model.classes, [0, 1, 2])

    def test_load_old_bundle(self):
        X, y, _ = toy_stats()
        pipe = make_pipeline("rf", 0)
        pipe.fit(X, y)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.pkl")
            with open(path, "wb") as f:  # 共通の形を入れる前の形式（"pipeline" だけ）
                pickle.dump({"pipeline": pipe, "algo": "rf", "classes": [0, 1, 2]}, f)
            bundle = load_bundle(path)
        self.assertEqual(bundle["input_kind"], "stats")
        np.testing.assert_array_equal(bundle["model"].predict_proba(X), pipe.predict_proba(X))


@unittest.skipUnless(HAS_TORCH, "PyTorchが入っていない")
class DeepModelTest(unittest.TestCase):
    def _config(self):
        from models.deep import TrainConfig
        return TrainConfig(max_epochs=8, patience=3, batch_size=16)

    def test_mlp_learns_and_pickles(self):
        X, y, g = toy_stats()
        model = create_model("mlp", 0, X.shape[1:], self._config())
        model.fit(X, y, g)
        self.assertGreater((model.predict(X) == y).mean(), 0.9)
        restored = pickle.loads(pickle.dumps(model))
        np.testing.assert_allclose(restored.predict_proba(X), model.predict_proba(X), atol=1e-6)

    def test_cnns_accept_window_input(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(24, 12, 3, 8)).astype(np.float32)
        y = np.array([0, 1, 2] * 8)
        X[y == 1] += 1.0
        g = np.array([1] * 12 + [2] * 12)
        for algo in ("cnn1d", "cnn2d"):
            self.assertEqual(INPUT_KIND[algo], "window")
            model = create_model(algo, 0, X.shape[1:], self._config())
            model.fit(X, y, g)
            proba = model.predict_proba(X[:5])
            self.assertEqual(proba.shape, (5, 3))
            np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-5)

    @unittest.skipUnless(HAS_TORCH and torch.cuda.is_available(), "GPU（CUDA）が無い（Google Colab で実行される）")
    def test_gpu_training_loads_on_cpu(self):
        from models.deep import TrainConfig
        rng = np.random.default_rng(0)
        X = rng.normal(size=(48, 12, 3, 8)).astype(np.float32)
        y = np.array([0, 1, 2] * 16)
        X[y == 1] += 1.0
        g = np.array([1] * 24 + [2] * 24)
        for algo in ("mlp", "cnn1d", "cnn2d"):
            Xa = X.reshape(len(X), -1) if algo == "mlp" else X
            cfg = TrainConfig(max_epochs=4, patience=2, batch_size=16, device="cuda")
            model = create_model(algo, 0, Xa.shape[1:], cfg)
            model.fit(Xa, y, g)
            self.assertEqual(next(model.net.parameters()).device.type, "cuda")
            restored = pickle.loads(pickle.dumps(model))  # 保存したモデルはCPUに載る（PCで使える）
            self.assertEqual(next(restored.net.parameters()).device.type, "cpu")
            np.testing.assert_allclose(restored.predict_proba(Xa), model.predict_proba(Xa), atol=1e-4)

    def test_same_seed_same_result(self):
        X, y, g = toy_stats()
        a = create_model("mlp", 7, X.shape[1:], self._config())
        b = create_model("mlp", 7, X.shape[1:], self._config())
        a.fit(X, y, g)
        b.fit(X, y, g)
        np.testing.assert_allclose(a.predict_proba(X), b.predict_proba(X), atol=1e-6)


if __name__ == "__main__":
    unittest.main()
