"""Only branded artifacts ship; cleanup preserves historical and sole installers."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_assets import cleanup_candidates, validate  # noqa: E402

canonical = {"id": 1, "name": "ClipRadar-Setup.exe"}
legacy = {"id": 2, "name": "VodClipper-Setup.exe"}
release = {"tag_name": "v4.18.2", "assets": [canonical, legacy]}
assert cleanup_candidates(release) == [legacy]
assert cleanup_candidates(dict(release, tag_name="v3.11.0")) == []
assert cleanup_candidates(dict(release, assets=[legacy])) == []
assert cleanup_candidates(dict(release, draft=True)) == []
assert cleanup_candidates(dict(release, tag_name="models-v1")) == []
assert cleanup_candidates(dict(release, assets=[canonical, {"id": 3, "name": "VodClipper-source.zip"}])) == []
with tempfile.TemporaryDirectory() as tmp:
    files = [str(Path(tmp) / name) for name in ("ClipRadar-Setup.exe", "ClipRadar-portable.zip", "ClipRadar-4.18.3-source.zip")]
    for file in files:
        Path(file).write_bytes(b"artifact")
    validate("4.18.3", files)
    for invalid in (files + [str(Path(tmp) / legacy["name"])], files[:-1], [*files[:-1], files[0]]):
        try:
            validate("4.18.3", invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid release accepted: {invalid}")
    Path(files[0]).write_bytes(b"")
    try:
        validate("4.18.3", files)
    except ValueError:
        pass
    else:
        raise AssertionError("Empty installer accepted")
print("PASSED: release branding, duplicate cleanup and historical preservation")
