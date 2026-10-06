"""`auditguard serve`: the viewer on http://127.0.0.1:<port>/viewer/ with data.js re-rendered on change."""

from __future__ import annotations

import functools
import http.server
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from .render_html import render_viewer
from .store import Store


def _journal_mtimes(audit: Path) -> float:
    newest = 0.0
    sprints = audit / "sprints"
    if sprints.is_dir():
        for p in sprints.rglob("journal.jsonl"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
    report = audit / "verify-report.json"
    if report.is_file():
        newest = max(newest, report.stat().st_mtime)
    return newest


def serve(root: Path, cfg: Any, port: int, open_browser: bool) -> int:
    audit = cfg.audit_root
    render_viewer(Store(root, cfg), cfg)
    stop = threading.Event()

    def watch() -> None:
        seen = _journal_mtimes(audit)
        while not stop.is_set():
            time.sleep(2)
            now = _journal_mtimes(audit)
            if now != seen:
                seen = now
                try:
                    render_viewer(Store(root, cfg), cfg)
                except Exception as exc:  # noqa: BLE001 - keep serving
                    print(f"auditGuard serve: re-render failed: {exc}")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            pass

    handler = functools.partial(Handler, directory=str(audit))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/viewer/index.html"
    print(f"auditGuard serve: {url}  (Ctrl+C to stop; data.js is re-rendered when a journal changes)")
    threading.Thread(target=watch, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
    return 0
