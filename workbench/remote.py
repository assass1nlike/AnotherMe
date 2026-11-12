"""通过本机 ssh 只读地查看服务器：GPU、tmux 会话、项目的 git 记录和实验记录。"""

import json
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Config

_cache: dict[str, tuple[float, dict]] = {}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class RemoteError(Exception):
    pass


def run(cfg: Config, server: str, cmd: str, timeout: int = 40, stdin: bytes | None = None) -> str:
    host = cfg.server(server).host
    try:
        p = subprocess.run(
            [cfg.ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, cmd],
            input=stdin, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RemoteError(f"{server} 连接超时")
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode == 255:
        raise RemoteError(f"{server} 连接失败：{p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return out


SEP = "\x1e"


def status(cfg: Config, server: str, max_age: float = 30) -> dict:
    hit = _cache.get(server)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    cmd = (
        "nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null;"
        f"echo '{SEP}';"
        "tmux list-panes -a -F '#{session_name}\t#{pane_current_path}\t#{pane_current_command}\t#{session_activity}' 2>/dev/null"
    )
    try:
        out = run(cfg, server, cmd)
    except RemoteError as e:
        res = {"server": server, "ok": False, "error": str(e), "checked": time.time()}
        _cache[server] = (time.time(), res)
        return res
    gpu_txt, _, tmux_txt = out.partition(SEP)
    gpus = []
    for line in gpu_txt.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 4:
            gpus.append({"index": int(parts[0]), "util": int(parts[1]), "mem": int(parts[2]), "total": int(parts[3])})
    sessions, seen = [], set()
    for line in tmux_txt.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[0] not in seen:
            seen.add(parts[0])
            sessions.append({"name": parts[0], "path": parts[1], "command": parts[2],
                             "activity": int(parts[3]) if parts[3].isdigit() else None})
    res = {"server": server, "ok": True, "gpus": gpus, "tmux": sessions, "checked": time.time()}
    _cache[server] = (time.time(), res)
    return res


def scan_projects(cfg: Config, server: str) -> list[dict]:
    """在配置的 roots 下找 git 仓库，排除隐藏目录、第三方代码和缓存。"""
    roots = " ".join(shlex.quote(r) for r in cfg.server(server).roots)
    cmd = (
        f"find {roots} -maxdepth 4 -name .git "
        "-not -path '*/.*/*' -not -path '*/third_party/*' -not -path '*/upstream/*' "
        "-not -path '*/node_modules/*' -not -path '*/miniconda3/*' 2>/dev/null | while read g; do "
        "r=$(dirname \"$g\"); "
        f"printf '%s\\t%s\\t%s\\n' \"$r\" \"$(git -C \"$r\" log -1 --format='%ci\t%s' 2>/dev/null)\" "
        "\"$(test -f \"$r/exps.md\" && echo 1 || echo 0)\"; done"
    )
    out = run(cfg, server, cmd, timeout=90)
    res = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            res.append({"path": parts[0], "last_commit": parts[1][:16], "subject": parts[2][:100],
                        "has_exps": parts[-1] == "1"})
    res.sort(key=lambda x: x["last_commit"], reverse=True)
    return res


def project_activity(cfg: Config, server: str, path: str, since: str = "3 days ago") -> str:
    p = shlex.quote(path)
    cmd = (
        f"cd {p} || exit 1; echo '## git log since {since}'; "
        f"git log --since={shlex.quote(since)} --format='%ci %s' 2>/dev/null | head -40; "
        "echo; echo '## 未提交的改动'; git status --short 2>/dev/null | head -20; "
        "echo; echo '## exps.md 末尾'; test -f exps.md && tail -n 60 exps.md"
    )
    return run(cfg, server, cmd, timeout=60)


def read_file(cfg: Config, server: str, path: str, lines: int = 120, from_end: bool = False,
              start_line: int | None = None) -> str:
    p = shlex.quote(path)
    if start_line:
        return run(cfg, server, f"sed -n '{int(start_line)},{int(start_line) + int(lines) - 1}p' {p}")
    tool = "tail" if from_end else "head"
    return run(cfg, server, f"{tool} -n {int(lines)} {p}")


def grep(cfg: Config, server: str, path: str, pattern: str, glob: str = "", max_lines: int = 150) -> str:
    """在项目里搜文本，带行号。git 仓库用 git grep（会包含未提交的改动），否则用 grep -r。"""
    p, pat = shlex.quote(path), shlex.quote(pattern)
    spec = f" -- {shlex.quote(glob)}" if glob else ""
    inc = f" --include={shlex.quote(glob)}" if glob else ""
    cmd = (f"cd {p} && (git rev-parse --git-dir >/dev/null 2>&1 && git grep -n -I -E {pat}{spec} "
           f"|| grep -rn -I -E {pat}{inc} --exclude-dir=.git .) 2>/dev/null | cut -c1-300 | head -n {int(max_lines)}")
    return run(cfg, server, cmd, timeout=60) or "没有匹配"


# ---------- 项目档案同步用 ----------

def git(cfg: Config, server: str, path: str, args: str, timeout: int = 60) -> str:
    return run(cfg, server, f"cd {shlex.quote(path)} && git {args}", timeout=timeout)


def head_commit(cfg: Config, server: str, path: str) -> str:
    return git(cfg, server, path, "rev-parse HEAD 2>/dev/null").strip()


def has_commit(cfg: Config, server: str, path: str, commit: str) -> bool:
    return git(cfg, server, path, f"cat-file -t {shlex.quote(commit)} 2>/dev/null").strip() == "commit"


def overview(cfg: Config, server: str, path: str) -> str:
    """首次建档用：目录、说明文档开头、完整的提交历史。"""
    p = shlex.quote(path)
    cmd = (
        f"cd {p} || exit 1; echo '## 顶层目录'; ls; "
        "for f in README.md AGENTS.md CLAUDE.md; do test -f $f && { echo; echo \"## $f（开头）\"; head -n 120 $f; }; done; "
        "echo; echo '## 提交历史（新到旧）'; git log --format='%h %ci %s' 2>/dev/null | head -n 300"
    )
    return run(cfg, server, cmd, timeout=90)


def read_whole(cfg: Config, server: str, path: str) -> str:
    return run(cfg, server, f"cat {shlex.quote(path)} 2>/dev/null", timeout=120)


def changes_since(cfg: Config, server: str, path: str, commit: str) -> str:
    """增量同步用：自上次同步以来的提交和改动文件统计。exps.md 的改动另外在本地比对。"""
    c = shlex.quote(commit)
    cmd = (
        f"cd {shlex.quote(path)} || exit 1; "
        f"echo '## 新提交'; git log {c}..HEAD --format='%h %ci %s' | head -n 200; "
        f"echo; echo '## 改动文件统计'; git diff --stat=160 {c} -- . ':(exclude)exps.md' | tail -n 60"
    )
    return run(cfg, server, cmd, timeout=120)


def codex_dialogue(cfg: Config, server: str, path: str, since: float = 0, words: list[str] | None = None) -> list[dict]:
    """服务器上在这个项目目录里开的 Codex 会话中，用户和 Codex 的对话（不含工具输出和过程旁白）。"""
    script = (Path(__file__).parent / "codex_extract.py").read_bytes()
    args = " ".join(shlex.quote(a) for a in [path, str(int(since))] + (words or []))
    out = run(cfg, server, f"python3 - {args}", timeout=300, stdin=script)
    rows = []
    for line in out.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    for r in rows:  # 记录里是 UTC，换成本机时间
        r["ts"] = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    return rows


def tmux_tail(cfg: Config, server: str, session: str, lines: int = 40) -> str:
    return run(cfg, server, f"tmux capture-pane -p -t {shlex.quote(session)} -S -{int(lines)} | sed '/^$/d' | tail -n {int(lines)}")
