"""Start the web tool:  python -m geosyntra_bridge.server [--host 127.0.0.1] [--port 8600]"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

RESTART_CODE = 3      # the Windows launcher starts the server again when it exits with this code


def _code_stamp(root: str) -> float:
    m = 0.0
    for d, _, fs in os.walk(root):
        if "__pycache__" in d:
            continue
        for f in fs:
            if f.endswith(".py"):
                try:
                    m = max(m, os.path.getmtime(os.path.join(d, f)))
                except OSError:
                    pass
    return m


def _watch_code(app):
    """An updated program (new .py files) takes effect by itself: when the code changes and no conversion is
    running, the server exits with RESTART_CODE and the launcher starts it again on the new code."""
    root = os.path.dirname(os.path.abspath(__file__))
    start = _code_stamp(root)
    while True:
        time.sleep(3)
        if _code_stamp(root) <= start:
            continue
        time.sleep(4)                       # let a multi-file update finish
        jobs = getattr(app.state, "jobs", None)
        def busy():
            # a conversion running now (state updated in the last 2 minutes) — a job left "running" by a crash is not
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for j in (jobs.list_jobs("", True, 1000) if jobs and hasattr(jobs, "list_jobs") else []):
                if j.get("status") in ("queued", "running"):
                    try:
                        if (now - datetime.fromisoformat(j.get("updated_at"))).total_seconds() < 120:
                            return True
                    except Exception:  # noqa: BLE001
                        pass
            return False
        for _ in range(150):
            try:
                if not busy():
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(2)
        print("GeoSyntra Bridge was updated — restarting on the new version", flush=True)
        os._exit(RESTART_CODE)


def main():
    ap = argparse.ArgumentParser(prog="geosyntra_bridge.server")
    ap.add_argument("--host", default=os.environ.get("GSB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("GSB_PORT", "8600")))
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("--watch", action="store_true", help="restart by itself when the program files are updated")
    a = ap.parse_args()
    import uvicorn
    from .api.app import create_app
    from .config import settings
    app = create_app(settings)
    url = f"http://{'localhost' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{a.port}{settings.base_path}/"
    print(f"GeoSyntra Bridge — CAD to GDB Schema Converter running at {url}")
    if a.open:
        webbrowser.open(url)
    if a.watch:
        threading.Thread(target=_watch_code, args=(app,), daemon=True).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
