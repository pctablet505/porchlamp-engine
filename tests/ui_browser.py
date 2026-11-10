"""A headless Chrome probe for layout tests: measure the real page at a real width.

``probe(site, cases)`` serves a built static site on loopback the way GitHub Pages does (``scripts/serve.py``'s
``StaticSiteHandler``: an address with no file gets ``404.html``), opens each case in an ``<iframe>`` of an exact width
(an iframe's width is the page's layout viewport, so the stylesheet's media queries see it), runs the case's script
inside the page and returns what the scripts returned. Chrome does the rendering; nothing is mocked.

A case is ``{"name", "path", "width", "height", "script"}``. ``script`` is the body of an async JavaScript function
with ``frame`` (the iframe), ``win``, ``doc``, ``until(fn, ms)`` and ``sleep(ms)`` in scope; ``until`` polls ``fn``
until it returns something truthy (or ``null`` after ``ms``).
"""

from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

_PAGE = """<!doctype html><meta charset="utf-8"><title>probe</title><body><pre id="result">pending</pre>
<script type="module">
const cases = %(cases)s;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(fn, ms = 8000) {
  const start = performance.now();
  while (performance.now() - start < ms) {
    try { const value = fn(); if (value) return value; } catch (err) { /* the page is still loading */ }
    await sleep(100);
  }
  return null;
}
const out = {};
for (const c of cases) {
  const frame = document.createElement("iframe");
  frame.style.cssText = `width:${c.width}px;height:${c.height}px;border:0;display:block`;
  document.body.appendChild(frame);
  await new Promise((resolve) => { frame.onload = resolve; frame.src = c.path; });
  const win = frame.contentWindow, doc = frame.contentDocument;
  try {
    out[c.name] = await (new Function("frame", "win", "doc", "until", "sleep",
      "return (async () => {" + c.script + "})();"))(frame, win, doc, until, sleep);
  } catch (err) {
    out[c.name] = { error: String(err && err.stack || err) };
  }
  frame.remove();
}
document.getElementById("result").textContent = JSON.stringify(out);
</script>"""


_MAC_CHROME_PATHS = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)


def find_chrome() -> str | None:
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in _MAC_CHROME_PATHS:
        if candidate.is_file():
            return str(candidate)
    return None


@functools.cache
def _site_handler() -> type:
    """``scripts/serve.py``'s ``StaticSiteHandler``, loaded by path: ``scripts/`` on ``sys.path`` would let
    ``scripts/serve.py`` shadow ``tests/serve``."""
    spec = importlib.util.spec_from_file_location("serve_script", Path(__file__).resolve().parent.parent / "scripts" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.StaticSiteHandler


class _SiteServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


@contextmanager
def serve(site: Path) -> Iterator[str]:
    handler = functools.partial(_site_handler(), directory=str(site))
    server = _SiteServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def probe(site: Path, cases: list[dict], tmp: Path, *, budget_ms: int = 60000, flags: tuple[str, ...] = (),
          env: dict[str, str] | None = None) -> dict:
    """Runs ``cases`` (module docstring) in one Chrome, with extra command-line ``flags`` (``--lang=de-DE``) and
    environment ``env`` (``TZ``) when given; returns what each case's script returned, by its name."""
    chrome = find_chrome()
    if chrome is None:
        raise FileNotFoundError("no Chrome or Chromium on PATH")
    import uuid
    probe_id = uuid.uuid4().hex
    probe_file = site / f"probe-{probe_id}.html"
    page = _PAGE % {"cases": json.dumps(cases)}
    probe_file.write_text(page, encoding="utf-8")
    profile_dir = tmp / f"chrome-profile-{probe_id}"
    try:
        with serve(site) as base:
            result = subprocess.run(
                [
                    chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                    f"--user-data-dir={profile_dir}", f"--virtual-time-budget={budget_ms}",
                    "--window-size=1400,1000", *flags, "--dump-dom", f"{base}/probe-{probe_id}.html",
                ],
                capture_output=True, text=True, timeout=180, check=False, env={**os.environ, **env} if env else None,
            )
    finally:
        probe_file.unlink(missing_ok=True)
        shutil.rmtree(profile_dir, ignore_errors=True)
    found = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.DOTALL)
    if not found or found.group(1) == "pending":
        raise RuntimeError(f"the probe page did not finish (exit {result.returncode}): {result.stderr[-600:]}")
    import html

    return json.loads(html.unescape(found.group(1)))
