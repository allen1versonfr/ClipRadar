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
        """Time-resolved: one pass over a long log-mel, a prediction every 32 frames (0.32 s).
        The CNN features are averaged over frequency and over a ~3 s neighbourhood in time, then
        fed through the original classifier head. That is the original model's global average
        pool, done locally, so each step means "what's happening in the 3 seconds around here"."""
        def __init__(self, m, k=9):
            super().__init__()
            self.features, self.k = m.features, k
            self.lin1, self.act, self.lin2 = m.classifier[2], m.classifier[3], m.classifier[5]

        def forward(self, x):
            f = self.features(x).mean(dim=2)                      # [B, C, T']
            f = torch.nn.functional.avg_pool1d(f, kernel_size=self.k, stride=1, padding=self.k // 2,
                                               count_include_pad=False)
            f = f.transpose(1, 2)                                 # [B, T', C]
            return torch.sigmoid(self.lin2(self.act(self.lin1(f))))   # [B, T', 527]

    net = Net(model).eval()
    out = os.path.join(os.path.abspath(a.out), "audio_events.onnx")
    dummy = torch.randn(1, 1, 128, 3000)
    kw = dict(input_names=["mel"], output_names=["probs"], opset_version=17,
              dynamic_axes={"mel": {0: "batch", 3: "frames"}, "probs": {0: "batch", 1: "steps"}})
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
    with torch.no_grad():
        ref_t = net(torch.from_numpy(my_mel)[None, None]).numpy()
    onnx_t = sess.run(None, {"mel": my_mel[None, None].astype(np.float32)})[0]
    report["steps_shape"] = list(onnx_t.shape)
    report["frames_per_step"] = my_mel.shape[1] / onnx_t.shape[1]
    report["probs_max_abs_diff"] = float(np.abs(onnx_t - ref_t).max())
    # the time-resolved output in the middle should agree with the original model on that window
    mid = onnx_t[0, onnx_t.shape[1] // 2]
    win = torch.from_numpy(my_mel[:, 150:450])[None, None]
    with torch.no_grad():
        ref_win = torch.sigmoid(model(win)[0]).numpy().reshape(-1)
    report["window_vs_steps_top5"] = [[int(i), round(float(ref_win[i]), 3), round(float(mid[i]), 3)]
                                      for i in np.argsort(-ref_win)[:5]]
    one = sess.run(None, {"mel": my_mel[None, None, :, :300].astype(np.float32)})[0]
    report["batch1_shape"] = list(one.shape)
    report["sha256"] = hashlib.sha256(open(out, "rb").read()).hexdigest()
    report["bytes"] = os.path.getsize(out)
    np.savez_compressed(os.path.join(os.path.abspath(a.out), "audio_events_test.npz"),
                        wave=wave, mel=ref_mel, probs=ref_probs, steps=ref_t)
    print(json.dumps(report, indent=1))
    with open(os.path.join(os.path.abspath(a.out), "audio_events_report.json"), "w") as f:
        json.dump(report, f, indent=1)
    ok = report["mel_max_abs_diff"] < 2e-3 and report["probs_max_abs_diff"] < 2e-3 and report["batch1_shape"][2] == 527
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
