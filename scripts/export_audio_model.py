"""
Builds src/models/audio_events.onnx: the laughter / scream / cheer detector.

It takes the pretrained EfficientAT MobileNetV3 "mn10_as" AudioSet model
(https://github.com/fschmid56/EfficientAT, MIT licence), exports the network
(log-mel in, 527 class probabilities out) to ONNX, and checks that
VodClipper's numpy front end (src/audio_events.py) and the ONNX model give the
same answers as the original PyTorch code.

  git clone https://github.com/fschmid56/EfficientAT
  pip install torch torchaudio onnx onnxruntime numpy
  python scripts/export_audio_model.py --efficientat EfficientAT --out out/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--efficientat", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="mn10_as")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.path.insert(0, os.path.abspath(a.efficientat))
    os.chdir(a.efficientat)          # its code expects to run from the repo root

    import torch
    from models.mn.model import get_model
    from models.preprocess import AugmentMelSTFT
    from helpers.utils import NAME_TO_WIDTH
    import audio_events as ae

    model = get_model(width_mult=NAME_TO_WIDTH(a.name), pretrained_name=a.name)
    model.eval()
    mel = AugmentMelSTFT(n_mels=128, sr=32000, win_length=800, hopsize=320)
    mel.eval()

    class Net(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.features, self.classifier = m.features, m.classifier

        def forward(self, x):
            return torch.sigmoid(self.classifier(self.features(x)))

    net = Net(model).eval()
    out = os.path.join(os.path.abspath(a.out), "audio_events.onnx")
    dummy = torch.randn(2, 1, 128, 300)
    kw = dict(input_names=["mel"], output_names=["probs"], opset_version=17,
              dynamic_axes={"mel": {0: "batch", 3: "frames"}, "probs": {0: "batch"}})
    try:
        torch.onnx.export(net, (dummy,), out, dynamo=False, **kw)
    except TypeError:
        torch.onnx.export(net, (dummy,), out, **kw)
    print("exported", out, os.path.getsize(out), "bytes")

    # ---- checks
    rng = np.random.default_rng(0)
    t = np.arange(32000 * 6) / 32000.0
    wave = (0.3 * np.sin(2 * np.pi * 440 * t) * (t > 2) + 0.05 * rng.standard_normal(t.size)).astype(np.float32)
    with torch.no_grad():
        ref_mel = mel(torch.from_numpy(wave)[None])[0].numpy()             # [128, T]
        x = torch.from_numpy(ref_mel)[None, None]
        ref_probs = torch.sigmoid(model(x)[0]).numpy().reshape(1, -1)
    my_mel = ae.logmel(wave)
    report = {"torch": torch.__version__, "mel_shape_ref": list(ref_mel.shape), "mel_shape_np": list(my_mel.shape)}
    n = min(ref_mel.shape[1], my_mel.shape[1])
    report["mel_max_abs_diff"] = float(np.abs(ref_mel[:, :n] - my_mel[:, :n]).max())

    import onnxruntime as ort
    sess = ort.InferenceSession(out, providers=["CPUExecutionProvider"])
    onnx_probs = sess.run(None, {"mel": my_mel[None, None, :, :n].astype(np.float32)})[0]
    report["probs_max_abs_diff"] = float(np.abs(onnx_probs - ref_probs).max())
    one = sess.run(None, {"mel": my_mel[None, None, :, :300].astype(np.float32)})[0]
    report["batch1_shape"] = list(one.shape)
    report["sha256"] = hashlib.sha256(open(out, "rb").read()).hexdigest()
    report["bytes"] = os.path.getsize(out)
    np.savez_compressed(os.path.join(os.path.abspath(a.out), "audio_events_test.npz"),
                        wave=wave, mel=ref_mel, probs=ref_probs)
    print(json.dumps(report, indent=1))
    with open(os.path.join(os.path.abspath(a.out), "audio_events_report.json"), "w") as f:
        json.dump(report, f, indent=1)
    ok = report["mel_max_abs_diff"] < 2e-3 and report["probs_max_abs_diff"] < 2e-3 and report["batch1_shape"] == [1, 527]
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
