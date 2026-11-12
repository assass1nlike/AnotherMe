"""带日期的文本：每个字符都记着它是哪天写的。

日期信息存在 determined.md 旁边的 .determined.dates.json 里，md 本身保持纯 markdown。
json 里同时存一份上次保存时的正文，用来发现 md 在别处被改过；改过的部分先标为 "?"，由界面让用户选日期。
"""

import difflib
import json
import os
from datetime import date
from pathlib import Path

EXCLUDE = {"skills", "ideas", "empirical"}
FILE = "determined.md"
SIDECAR = ".determined.dates.json"

Runs = list[list]  # [[长度, 日期或 None], ...]，None 表示用这个工具之前就有、不知道哪天写的


def root() -> Path:
    r = os.environ.get("RESE")
    if not r:
        import tomllib
        cfg = Path(__file__).resolve().parent.parent / "config.toml"
        if cfg.exists():
            r = tomllib.loads(cfg.read_text(encoding="utf-8")).get("progress", {}).get("root", "")
    if not r:
        raise RuntimeError("没有找到研究目录：请设置环境变量 RESE，或在 config.toml 的 [progress] 里填 root")
    return Path(r)


def documents() -> list[Path]:
    return sorted((d / FILE for d in root().iterdir()
                   if d.is_dir() and d.name not in EXCLUDE and (d / FILE).exists()), key=lambda p: p.parent.name.lower())


def explode(runs: Runs) -> list:
    out = []
    for n, d in runs:
        out.extend([d] * n)
    return out


def compress(dates: list) -> Runs:
    runs: Runs = []
    for d in dates:
        if runs and runs[-1][1] == d:
            runs[-1][0] += 1
        else:
            runs.append([1, d])
    return runs


def remap(old: str, old_dates: list, new: str, fill) -> list:
    """old 改成 new 之后，没变的字保留原来的日期，新增或改过的字记为 fill。先按行对齐，再在改动的行块里按字对齐。"""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    starts_a = [0]
    for line in a:
        starts_a.append(starts_a[-1] + len(line))
    out: list = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.extend(old_dates[starts_a[i1]:starts_a[i2]])
        elif tag == "insert":
            out.extend([fill] * sum(len(x) for x in b[j1:j2]))
        elif tag == "replace":
            sa, sb = "".join(a[i1:i2]), "".join(b[j1:j2])
            seg = old_dates[starts_a[i1]:starts_a[i2]]
            for t, k1, k2, l1, l2 in difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
                out.extend(seg[k1:k2] if t == "equal" else [fill] * (l2 - l1))
    assert len(out) == len(new)
    return out


class Doc:
    """一份 determined.md 和它的日期信息。text 统一用 \\n 换行，保存时还原文件原来的换行符。"""

    def __init__(self, path: Path):
        self.path = path
        self.sidecar = path.parent / SIDECAR
        raw = path.read_bytes().decode("utf-8")
        self.crlf = "\r\n" in raw
        self.text = raw.replace("\r\n", "\n")
        self.mtime = path.stat().st_mtime
        self.dates: list = [None] * len(self.text)
        self.external = False  # md 在别处被改过，改动部分暂时没有日期
        if self.sidecar.exists():
            meta = json.loads(self.sidecar.read_text(encoding="utf-8"))
            snap = meta["text"]
            snap_dates = explode(meta["runs"])
            if snap == self.text:
                self.dates = snap_dates
            else:
                self.dates = remap(snap, snap_dates, self.text, "?")
                self.external = True

    def save(self, text: str, dates: list):
        assert len(text) == len(dates)
        body = text.replace("\n", "\r\n") if self.crlf else text
        tmp = self.path.with_suffix(".md.tmp")
        tmp.write_bytes(body.encode("utf-8"))
        os.replace(tmp, self.path)
        self.sidecar.write_text(json.dumps({"text": text, "runs": compress(dates)}, ensure_ascii=False),
                                encoding="utf-8")
        self.text, self.dates, self.mtime = text, dates, self.path.stat().st_mtime

    def changed_on_disk(self) -> bool:
        return self.path.stat().st_mtime != self.mtime


def today() -> str:
    return date.today().isoformat()
