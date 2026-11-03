"""Private files under ~/.config/deploy: stored credentials (CL11) and the affinity cookie (CL38).

The directory is 0700 and the files 0600. Credentials that others can read are refused. The cookie is only
a routing hint, so a file that is unusable is ignored instead of failing a command.
"""
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from .errors import EXIT_AUTH, CliError


def config_dir(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return Path(base) / "deploy"


def write_private(path: Path, text: str) -> None:
    existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed:
        os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def readable_by_others(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o077)


class Credentials:
    """Access and refresh tokens per server: {server: {user, access_token, refresh_token, expires_at, refresh_expires_at}}."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        if readable_by_others(self.path):
            raise CliError(f"credentials file {self.path} can be read by other users", EXIT_AUTH,
                           f"Run: chmod 600 {self.path}  (or delete it and run `deploy login`)")
        try:
            data = json.loads(self.path.read_text())
            return data["servers"] if isinstance(data, dict) and isinstance(data.get("servers"), dict) else {}
        except (OSError, ValueError, KeyError):
            raise CliError("stored credentials are unreadable", EXIT_AUTH, "Run `deploy login` again.") from None

    def get(self, server: str) -> Optional[dict]:
        return self._load().get(server)

    def put(self, server: str, entry: dict) -> None:
        servers = self._load()
        servers[server] = entry
        write_private(self.path, json.dumps({"version": 1, "servers": servers}, indent=2))


class Cookies:
    """The affinity cookie, per server (D29). Sent only to the server that set it."""

    def __init__(self, path: Path):
        self.path = path
        self._data = self._read()
        self._dirty = False

    def _read(self) -> dict:
        try:
            if self.path.exists() and not readable_by_others(self.path):
                data = json.loads(self.path.read_text())
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            pass
        return {}

    def header_for(self, server: str) -> Optional[str]:
        jar = self._data.get(server) or {}
        return "; ".join(f"{k}={v}" for k, v in jar.items()) or None

    def update(self, server: str, cookies: Mapping[str, str]) -> None:
        if cookies and self._data.get(server, {}) != {**self._data.get(server, {}), **cookies}:
            self._data[server] = {**self._data.get(server, {}), **cookies}
            self._dirty = True

    def save(self) -> None:
        if self._dirty:
            try:
                write_private(self.path, json.dumps(self._data, indent=2))
            except OSError:
                pass  # losing the hint is harmless
