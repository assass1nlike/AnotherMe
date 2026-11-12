"""日期进度：查看和编辑研究目录下各项目的 determined.md，每个字记着它是哪天写的，按日期着色。

界面是网页，用 Edge 的应用模式打开成独立窗口；窗口关掉，程序就退出。只用标准库。
"""

import json
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store

PORT = 8766
WEB = Path(__file__).resolve().parent / "web"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}
last_seen = time.time()


def doc_by_name(name: str) -> Path:
    for p in store.documents():
        if p.parent.name == name:
            return p
    raise KeyError(f"没有项目 {name}")


def summary(p: Path) -> dict:
    """列表用：各日期的字数，不读入时不处理别处的改动。"""
    d = store.Doc(p)
    counts: dict = {}
    for x in d.dates:
        counts[x] = counts.get(x, 0) + 1
    return {"name": p.parent.name, "chars": len(d.text), "counts": {k or "": v for k, v in counts.items()},
            "external": d.external}


def load(name: str) -> dict:
    d = store.Doc(doc_by_name(name))
    return {"name": name, "text": d.text, "runs": store.compress(d.dates), "mtime": d.mtime, "external": d.external}


def save(body: dict) -> dict:
    p = doc_by_name(body["name"])
    d = store.Doc(p)
    if not body.get("force") and abs(d.mtime - body["mtime"]) > 1e-6:
        return {"conflict": True}
    text, dates = body["text"], store.explode(body["runs"])
    if len(text) != len(dates):
        raise ValueError("文字和日期长度不一致，没有保存")
    d.save(text, dates)
    return {"mtime": d.mtime}


def mtimes() -> dict:
    return {p.parent.name: p.stat().st_mtime for p in store.documents()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    def _guard(self, fn):
        global last_seen
        last_seen = time.time()
        try:
            self._json(fn())
        except KeyError as e:
            self._json({"error": str(e).strip("'")}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/docs":
            return self._guard(lambda: {"root": str(store.root()), "today": store.today(),
                                        "docs": [summary(p) for p in store.documents()]})
        if u.path == "/api/doc":
            return self._guard(lambda: load(q["name"]))
        if u.path == "/api/mtimes":
            return self._guard(mtimes)
        f = WEB / ("index.html" if u.path == "/" else u.path.lstrip("/"))
        if f.is_file() and WEB in f.resolve().parents:
            return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if urlparse(self.path).path == "/api/save":
            return self._guard(lambda: save(body))
        self._send(404, b"not found", "text/plain")


def browser() -> str | None:
    for c in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(c).exists():
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def open_window(url: str) -> subprocess.Popen | None:
    """独立的浏览器配置目录让这个窗口单独成一个进程，窗口关掉时进程结束，程序据此退出。"""
    exe = browser()
    if not exe:
        webbrowser.open(url)
        return None
    profile = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "workbench-progress" / "browser"
    return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                             "--no-default-browser-check", "--window-size=1500,960"])


def serve(port: int = PORT):
    """只开服务、不开窗口，调试界面用。"""
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # 已经开着一个，再开一个窗口连过去
        open_window(url)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    win = open_window(url)
    if win:
        win.wait()
    else:  # 没找到 Edge/Chrome 时用默认浏览器，页面关掉两分钟后退出
        while time.time() - last_seen < 120:
            time.sleep(5)
    server.shutdown()


if __name__ == "__main__":
    main()
