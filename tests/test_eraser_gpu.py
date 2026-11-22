"""
The song eraser on the graphics card (5.7.5, songerase._place): DirectML (NVIDIA, AMD and Intel alike) used when this copy
has it, the card gives real numbers and beats the processor; the choice kept for the card; Settings › Performance's
choice followed (Automatic, the graphics card, the CPU); a busy card left alone; and a card that fails partway handing
over to the processor. ONNX Runtime and the card are stood in for (no graphics card in CI).

  python tests/test_eraser_gpu.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_dml_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import gpu  # noqa: E402
import songerase  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


class FakeSession:
    made: list = []

    def __init__(self, path, so, providers):
        self.on = "gpu" if any((p[0] if isinstance(p, tuple) else p) == "DmlExecutionProvider" for p in providers) else "cpu"
        self.device = next((p[1]["device_id"] for p in providers if isinstance(p, tuple)), None)
        FakeSession.made.append((self.on, self.device))
        self.runs = 0

    def run(self, _, feed):
        self.runs += 1
        x = feed["mix_spec"]
        if self.on == "gpu" and CARD["broken"]:
            return [np.full((1, 6) + x.shape[2:], np.nan, dtype=np.float32)]
        if self.on == "gpu" and CARD["dies_after"] and self.runs > CARD["dies_after"]:
            raise RuntimeError("DXGI_ERROR_DEVICE_REMOVED")
        if self.on == "gpu":
            fake_clock[0] += CARD["block"]
        return [np.ones((1, 6) + x.shape[2:], dtype=np.float32)]


CARD = {"broken": False, "block": 3.0, "dies_after": 0}
fake_clock = [0.0]
ort = types.SimpleNamespace(
    get_available_providers=lambda: PROVIDERS[0], InferenceSession=FakeSession,
    SessionOptions=lambda: types.SimpleNamespace(add_session_config_entry=lambda *a: None),
    ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL=0))
PROVIDERS = [["DmlExecutionProvider", "CPUExecutionProvider"]]
sys.modules["onnxruntime"] = ort
import time as _time  # noqa: E402
real_pc = _time.perf_counter
_time.perf_counter = lambda: fake_clock[0]
gpu.card = lambda: {"name": "AMD Radeon RX 7800 XT", "vendor": "amd", "vram_mb": 16384}
gpu.cards = lambda: [gpu.card()]
gpu.gpu_load = lambda wait=True: LOAD[0]
LOAD = [5.0]
MODE = ["auto"]
gpu.mode = lambda: MODE[0]
songerase.model_path = lambda: "model.onnx"
songerase._cpu_block_seconds = lambda: 44.0


def place():
    songerase.release()
    songerase.session_impl[0] = None
    FakeSession.made.clear()
    return songerase._load()


try:
    m = place()
    check("an AMD card with DirectML, quicker than the processor: the song eraser runs on it", m.where == "gpu", (m.where, FakeSession.made))
    check("and says how much quicker", "3.0 s a block on the graphics card, against 44 s" in songerase.status()["why"], songerase.status())
    check("the choice is kept for this card", songerase._remembered().get("device") == "gpu")
    m = place()
    check("next time it's used without timing the card again", m.where == "gpu" and len(FakeSession.made) == 1, FakeSession.made)
    check("the export's estimate uses the card's speed", songerase.expected_block_seconds() == 3.0, songerase.expected_block_seconds())

    os.remove(songerase._device_file())
    CARD["block"] = 60.0
    m = place()
    check("a card slower than the processor: the processor", m.where == "cpu" and "processor is quicker" in songerase.status()["why"],
          songerase.status())
    m = place()
    check("and that's kept: the card isn't timed again", m.where == "cpu" and FakeSession.made == [("cpu", None)], FakeSession.made)
    MODE[0] = "gpu"
    m = place()
    check("unless you chose the graphics card in Settings", m.where == "gpu", m.where)
    MODE[0] = "cpu"
    m = place()
    check("you chose the CPU: the processor, the card not touched", m.where == "cpu" and FakeSession.made == [("cpu", None)], FakeSession.made)
    MODE[0] = "auto"

    os.remove(songerase._device_file())
    CARD.update(block=3.0, broken=True)
    m = place()
    check("a card that gives numbers that aren't numbers isn't used", m.where == "cpu" and "couldn't run it" in songerase.status()["why"],
          songerase.status())
    CARD["broken"] = False
    os.remove(songerase._device_file())

    LOAD[0] = 97.0
    m = place()
    check("a game has the card busy: the processor, this time", m.where == "cpu" and "busy" in songerase.status()["why"], songerase.status())
    LOAD[0] = 5.0

    PROVIDERS[0] = ["CPUExecutionProvider"]
    m = place()
    check("a copy without DirectML: the processor, and says why", m.where == "cpu" and "can't use the graphics card" in songerase.status()["why"])
    PROVIDERS[0] = ["DmlExecutionProvider", "CPUExecutionProvider"]

    CARD["dies_after"] = 3          # two runs to time it, then one real block, then the driver resets
    m = place()
    x = np.zeros((1, 2, 1025, 1034), dtype=np.float32)
    out1 = m.run(x)
    out2 = m.run(x)
    check("a card that stops partway: the block is done on the processor, and the export goes on",
          m.where == "cpu" and out1.shape == out2.shape and np.isfinite(out2).all(), m.where)
    check("and it says so", "goes on on the processor" in songerase.status()["note"], songerase.status())
    check("and the processor is used from then on for this card", songerase._remembered().get("device") == "cpu")
    st = gpu.song_eraser_status()
    check("Settings says where it runs and why", st["can_gpu"] and st["where"] == "cpu" and st["why"], st)
finally:
    _time.perf_counter = real_pc
    songerase.release()

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
