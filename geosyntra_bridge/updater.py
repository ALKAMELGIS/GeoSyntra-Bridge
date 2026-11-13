"""
Automatic updates from GitHub.

The running server checks the project's GitHub repository every half hour (and once at start). When the main branch
has a new commit, its files are downloaded and written over this installation — only files that changed, never
deleting anything, and never touching local data, the virtual environment, local tools or the launchers that carry
local settings. The code watcher (server --watch) then restarts on the new version, so an update needs nothing from
the user: no zip to download, no folder to replace.

Settings (environment):
  GSB_AUTO_UPDATE   1 (default) / 0 to switch it off
  GSB_UPDATE_REPO   owner/repo (default ALKAMELGIS/GeoSyntra-Bridge)
  GSB_UPDATE_BRANCH branch (default main)
  GSB_GITHUB_TOKEN  a read-only GitHub token, needed only when the repository is private
                    (or put it in github_token.txt next to the launchers)
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from typing import Any, Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, ".gsb_update.json")
REPO = os.environ.get("GSB_UPDATE_REPO", "ALKAMELGIS/GeoSyntra-Bridge")
BRANCH = os.environ.get("GSB_UPDATE_BRANCH", "main")
INTERVAL = 30 * 60

# never written by an update: local data, environments, local-only tools, launchers holding local settings
SKIP_DIRS = (".git/", ".venv/", "venv/", "data/", "data_accounts/", "data_commercial/", "output/", "web/node_modules/",
             "tools/libredwg/", "tools/oda/app/", "_previous_version/")
SKIP_FILES = {"start_accounts.bat", "start_windows_commercial.bat", "owner_email.txt", ".env", ".gsb_update.json", "github_token.txt"}

_lock = threading.Lock()
status: Dict[str, Any] = {"enabled": os.environ.get("GSB_AUTO_UPDATE", "1") != "0", "repo": REPO, "branch": BRANCH,
                          "checked_at": None, "latest": None, "installed": None, "last_update": None, "error": None}


def _token() -> str:
    t = os.environ.get("GSB_GITHUB_TOKEN", "")
    if not t:
        try:
            with open(os.path.join(ROOT, "github_token.txt"), encoding="utf-8") as f:
                t = f.read()
        except OSError:
            pass
    return t.strip()


def _get(url: str, timeout: int = 30) -> bytes:
    h = {"User-Agent": "git/2.40 GeoSyntra-Bridge-updater"}
    tok = _token()
    if tok:
        import base64
        h["Authorization"] = ("Basic " + base64.b64encode(f"x-access-token:{tok}".encode()).decode()
                              if url.startswith("https://github.com/") else f"Bearer {tok}")
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https GitHub URLs
        return r.read()


def _state() -> Dict[str, Any]:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def latest_sha() -> str:
    """The branch head from git's own ref advertisement (no GitHub API: no rate limit, follows renames)."""
    import re
    txt = _get(f"https://github.com/{REPO}.git/info/refs?service=git-upload-pack").decode("latin-1")
    m = re.search(r"([0-9a-f]{40}) refs/heads/" + re.escape(BRANCH) + r"\b", txt)
    if not m:
        raise RuntimeError(f"branch {BRANCH} not found on GitHub")
    return m.group(1)


def _skip(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    return rel in SKIP_FILES or any(rel.startswith(p) for p in SKIP_DIRS) or "/__pycache__/" in f"/{rel}"


def apply(sha: str) -> Dict[str, Any]:
    """Download the commit and write every changed file over this installation (atomically, file by file)."""
    url = (f"https://api.github.com/repos/{REPO}/zipball/{sha}" if _token()   # private: through the API with the token
           else f"https://codeload.github.com/{REPO}/zip/{sha}")
    data = _get(url, timeout=300)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if not n.endswith("/")]
    top = names[0].split("/", 1)[0] + "/" if names else ""
    changed, req_changed = [], False
    for n in names:
        rel = n[len(top):]
        if not rel or _skip(rel):
            continue
        dst = os.path.join(ROOT, *rel.split("/"))
        new = z.read(n)
        try:
            with open(dst, "rb") as f:
                if f.read() == new:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".gsbtmp"
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, dst)
        changed.append(rel)
        req_changed |= rel == "requirements.txt"
    if req_changed:      # new dependencies: install them before the restart picks up the new code
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "requirements.txt")],
                           check=True, timeout=1800)
            mark = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "requirements.installed")
            if os.path.isdir(os.path.dirname(mark)):
                with open(os.path.join(ROOT, "requirements.txt"), "rb") as s, open(mark, "wb") as d:
                    d.write(s.read())
        except Exception as e:  # noqa: BLE001
            status["error"] = f"dependencies: {e}"
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"sha": sha, "at": time.time(), "changed": len(changed)}, f)
    return {"sha": sha, "changed": changed}


def check_and_update(force: bool = False) -> Dict[str, Any]:
    with _lock:
        try:
            sha = latest_sha()
            status.update(checked_at=time.time(), latest=sha, error=None)
            cur = _state().get("sha")
            status["installed"] = cur
            if sha == cur and not force:
                return {"updated": False, "sha": sha}
            r = apply(sha)
            status.update(installed=sha, last_update={"at": time.time(), "files": len(r["changed"]), "sha": sha})
            if r["changed"]:
                print(f"GeoSyntra Bridge: updated from GitHub ({len(r['changed'])} files, {sha[:7]})", flush=True)
            return {"updated": bool(r["changed"]), "sha": sha, "files": r["changed"][:50], "count": len(r["changed"])}
        except Exception as e:  # noqa: BLE001 - offline, rate-limited… try again next round
            msg = str(e)[:300]
            if "404" in msg and not _token():
                msg += " — the GitHub repository is private: make it public or add a read-only token in github_token.txt"
            status.update(checked_at=time.time(), error=msg)
            return {"updated": False, "error": status["error"]}


def _loop():
    time.sleep(20)                         # let the server come up first
    while True:
        check_and_update()
        time.sleep(INTERVAL)


def start() -> Optional[threading.Thread]:
    if not status["enabled"] or os.path.isdir(os.path.join(ROOT, ".git")):   # a git checkout updates with git
        status["enabled"] = False
        return None
    status["installed"] = _state().get("sha")
    t = threading.Thread(target=_loop, daemon=True, name="gsb-updater")
    t.start()
    return t
