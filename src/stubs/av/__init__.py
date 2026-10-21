"""
Stand-in for PyAV in the packaged app.

faster-whisper imports PyAV when it loads, but only uses it to read audio *files*. Clip Radar
never hands it a file: FFmpeg (already bundled) decodes the audio and Whisper gets the samples
(engine.extract_audio). Shipping PyAV would add a second 64 MB+ copy of FFmpeg's libraries, so
the build leaves it out and puts this module in its place. Running from source uses the real
PyAV when it's installed; this folder is only added to the packaged build.
"""

STANDIN = True
__version__ = "stand-in (no PyAV in the packaged app)"


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)          # import machinery and getattr(..., default) probes
    raise RuntimeError("Reading audio files directly isn't part of this build of Clip Radar. "
                       "(Internal: pass decoded samples to Whisper, not a file path.)")
