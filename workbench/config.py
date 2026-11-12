import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("WORKBENCH_DATA") or ROOT / "data")


@dataclass
class ModelCfg:
    name: str
    provider: str  # "anthropic" | "openai"
    model: str
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""

    def key(self) -> str:
        return self.api_key or os.environ.get(self.api_key_env, "")


@dataclass
class ServerCfg:
    name: str
    host: str
    roots: list[str] = field(default_factory=list)


@dataclass
class Config:
    models: list[ModelCfg]
    default_model: str
    servers: list[ServerCfg]
    port: int
    ssh: str
    sync_model: str = ""
    codex: bool = True

    def model(self, name: str | None) -> ModelCfg:
        name = name or self.default_model
        for m in self.models:
            if m.name == name:
                return m
        raise KeyError(f"没有名为 {name} 的模型配置")

    def server(self, name: str) -> ServerCfg:
        for s in self.servers:
            if name in (s.name, s.host):
                return s
        raise KeyError(f"没有名为 {name} 的服务器配置")


def _default_ssh() -> str:
    win = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
    return str(win) if win.exists() else (shutil.which("ssh") or "ssh")


def load() -> Config:
    path = ROOT / "config.toml"
    if not path.exists():
        path = ROOT / "config.example.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    app = raw.get("app", {})
    return Config(
        models=[ModelCfg(**m) for m in raw.get("models", [])],
        default_model=raw.get("default_model", ""),
        servers=[ServerCfg(**s) for s in raw.get("servers", [])],
        port=app.get("port", 8765),
        ssh=app.get("ssh") or _default_ssh(),
        sync_model=app.get("sync_model") or raw.get("default_model", ""),
        codex=app.get("codex", True),
    )
