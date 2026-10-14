"""A headless Chrome probe for layout tests: measure the real page at a real width.

``probe(site, cases)`` serves a built static site on loopback, opens each case in an ``<iframe>`` of an exact width
(an iframe's width is the page's layout viewport, so the stylesheet's media queries see it), runs the case's script
inside the page and returns what the scripts returned. Chrome does the rendering; nothing is mocked.

A case is ``{"name", "path", "width", "height", "script"}``. ``script`` is the body of an async JavaScript function
with ``frame`` (the iframe), ``win``, ``doc``, ``until(fn, ms)`` and ``sleep(ms)`` in scope; ``until`` polls ``fn``
until it returns something truthy (or ``null`` after ``ms``).
"""

from __future__ import annotations

import functools
import http.server
import json
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


@contextmanager
def serve(site: Path) -> Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # the base class's signature; the probe's requests are not worth logging
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def probe(site: Path, cases: list[dict], tmp: Path, *, budget_ms: int = 60000) -> dict:
    chrome = find_chrome()
    if chrome is None:
        raise FileNotFoundError("no Chrome or Chromium on PATH")
    page = _PAGE % {"cases": json.dumps(cases)}
    (site / "probe.html").write_text(page, encoding="utf-8")
    with serve(site) as base:
        result = subprocess.run(
            [
                chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                f"--user-data-dir={tmp / 'chrome-profile'}", f"--virtual-time-budget={budget_ms}",
                "--window-size=1400,1000", "--dump-dom", f"{base}/probe.html",
            ],
            capture_output=True, text=True, timeout=180, check=False,
        )
    (site / "probe.html").unlink(missing_ok=True)
    found = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.DOTALL)
    if not found or found.group(1) == "pending":
        raise RuntimeError(f"the probe page did not finish (exit {result.returncode}): {result.stderr[-600:]}")
    import html

    return json.loads(html.unescape(found.group(1)))
