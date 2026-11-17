"""
Checking TikTok Studio's upload page without uploading anything (studio_auto.check_page, scripts/studio_check.py),
against the stand-in for TikTok Studio's page (tests/fake_studio.py) in a real browser: Edge or Chrome when
installed, else Playwright's Chromium.

The page as it should be passes, and nothing is put in or pressed. A page without the upload box fails on that
part. Signed out is said. With a test video (--file), the caption box and Post are checked, Post is never pressed,
and the page is left empty again. The script saves what it saw as a fingerprint file.

  python tests/test_studio_check.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_STUDIO_HEADLESS"] = "1"
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import cdp  # noqa: E402
import fake_studio  # noqa: E402
import studio_auto  # noqa: E402
import studio_check  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# without a browser: the verdict on a fingerprint
sel = {"file": {"match": True, "visible": False, "enabled": True}, "login": {"match": False},
       "upload_error": {"match": False}, "posted": {"match": False}}
check("an upload page with its file box passes", all(ok for _, ok, _ in studio_auto.check_fingerprint({"selectors": sel}, "tiktok")))
bad = dict(sel, file={"match": False})
rows = studio_auto.check_fingerprint({"selectors": bad}, "tiktok")
check("one without it fails on the file box, and only there", [n for n, ok, _ in rows if not ok] == ["file"], rows)
rows = studio_auto.check_fingerprint({"selectors": dict(sel, login={"match": False, "error": True})}, "tiktok")
check("a selector whose script threw fails", [n for n, ok, _ in rows if not ok] == ["login"], rows)
rows = studio_auto.check_fingerprint(None, "tiktok")
check("no fingerprint at all fails every part", rows and not any(ok for _, ok, _ in rows), rows)
try:
    studio_auto.check_page("youtube", "http://127.0.0.1:9/", file=__file__)
    check("a test video is never put in on YouTube (it makes a draft)", False)
except ValueError:
    check("a test video is never put in on YouTube (it makes a draft)", True)

if not studio_auto.find_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        if os.path.isfile(exe):
            os.environ["CLIPRADAR_STUDIO_BROWSER"] = exe
    except Exception:
        pass
if not studio_auto.find_browser():
    print("SKIPPED: no Edge, Chrome or Playwright Chromium here")
    sys.exit(1 if failures else 0)
print("browser:", studio_auto.find_browser())

tmp = tempfile.mkdtemp(prefix="vc_check_")
clip = os.path.join(tmp, "test clip.mp4")
with open(clip, "wb") as f:
    f.write(os.urandom(50_000))


def page_now(srv):
    """What the fake page shows now: a file in its box, the editor, the Post now question."""
    page = cdp.Page.connect(studio_auto._state["port"], url_prefix=srv.url.split("/tiktokstudio")[0])
    try:
        return page.eval("({files: (document.getElementById('f') || {files: []}).files.length,"
                         " editor: !!document.getElementById('editor') && !document.getElementById('editor').hidden,"
                         " confirm: !!document.getElementById('confirm') && !document.getElementById('confirm').hidden,"
                         " path: location.pathname})")
    finally:
        page.close()


try:
    # 1. the page as it should be: every part passes, and nothing is put in or pressed
    srv = fake_studio.start()
    steps = []
    r = studio_auto.check_page("tiktok", srv.url, wait=20, step=steps.append)
    check("the upload page as it should be passes", r["ok"] and r["signed_in"], r["results"])
    check("every part an upload looks for is checked", [n for n, _, _ in r["results"]] == list(studio_auto.EXPECT["tiktok"]["page"]))
    now = page_now(srv)
    check("nothing is put in and nothing pressed", now["files"] == 0 and not now["editor"] and not now["confirm"]
          and not srv.posted, now)
    check("the fingerprint has no account", "Okonkwo" not in json.dumps(r["page"], ensure_ascii=False), r["page"])

    # 2. with a test video: the caption box and Post are checked, Post isn't pressed, and the page is left empty again
    r = studio_auto.check_page("tiktok", srv.url, file=clip, wait=20, step=steps.append)
    check("with a test video, the caption box and Post pass", r["ok"] and r["file_results"]
          and all(ok for _, ok, _ in r["file_results"]), r["file_results"])
    check("the caption box is seen with the video in", r["file"]["selectors"]["caption"]["visible"], r["file"]["selectors"])
    now = page_now(srv)
    check("Post is never pressed, and the page is empty again", not srv.posted and not now["confirm"]
          and now["files"] == 0 and not now["editor"], now)
    check("the test video's name isn't in the fingerprint", "test clip" not in json.dumps(r["file"]), r["file"].get("buttons"))
    srv.shutdown()

    # 3. TikTok changed the page: the file box fails, and the check says so
    srv = fake_studio.start(mode="changed")
    r = studio_auto.check_page("tiktok", srv.url, wait=3)
    check("a page without the upload box fails on the file box", not r["ok"]
          and [n for n, ok, _ in r["results"] if not ok] == ["file"], r["results"])
    check("and its fingerprint shows what the page has instead", "new_upload_button" in r["page"].get("marks", []), r["page"])
    srv.shutdown()

    # 4. signed out: said, and a fail
    srv = fake_studio.start(mode="login")
    r = studio_auto.check_page("tiktok", srv.url, wait=10)
    check("signed out is said, and fails", not r["signed_in"] and not r["ok"], r)
    check("its login part shows TikTok asking", dict((n, ok) for n, ok, _ in r["results"]).get("login") is False, r["results"])
    srv.shutdown()

    # 5. the script: prints PASS per part, saves the fingerprint, exit code 0; a changed page exits 1
    srv = fake_studio.start()
    out = tempfile.mkdtemp(prefix="vc_fp_")
    code = studio_check.main(["--url", srv.url, "--save-dir", out, "--wait", "20", "--file", clip])
    saved = sorted(os.listdir(out))
    check("the script passes and saves a dated fingerprint", code == 0 and len(saved) == 1 and saved[0].endswith(".json"),
          (code, saved))
    data = json.load(open(os.path.join(out, saved[0]), encoding="utf-8")) if saved else {}
    check("with the page and the page with a video in", data.get("page", {}).get("selectors") and data.get("file", {}).get("selectors")
          and data.get("site") == "tiktok", list(data))
    check("a second one the same day doesn't overwrite it", studio_check.saved_name("tiktok", saved[0][:10], out) != saved[0])
    check("nothing was posted", not srv.posted)
    srv.shutdown()
    srv = fake_studio.start(mode="changed")
    code = studio_check.main(["--url", srv.url, "--no-save", "--wait", "3"])
    check("a changed page: the script exits 1", code == 1, code)
    srv.shutdown()
finally:
    p = studio_auto._state.get("proc")
    if p:
        p.kill()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
