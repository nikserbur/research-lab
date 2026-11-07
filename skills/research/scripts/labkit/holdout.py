"""Keeping the holdout unread: `lab.py holdout protect` records each holdout file's hash and mode and
takes every permission away from it (mode 000), so no program run as this user can open it: not a
tool call, not a helper script, not a glob, a copy, a hard link or a symlink. `holdout open` gives the
permissions back for the one look and `close` takes them away again; `release` returns them for good
once the holdout is used up. The research_guard hook adds a readable refusal on top.

What it cannot stop: someone who runs chmod on purpose, a copy made before `protect`, a process that
already had the file open, or Windows, where a mode only sets the read-only flag.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import stat

from .ledger import Ledger, sha256_bytes

LOCKED = 0o000


def home() -> pathlib.Path:
    env = os.environ.get("RESEARCH_LAB_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "research-lab"


def register_ledger(ledger: Ledger) -> None:
    """The hook finds research folders under <project>/research/*; this list covers the others."""
    registry = home() / "protected-ledgers.txt"
    path = str(ledger.path.resolve())
    try:
        known = registry.read_text(encoding="utf-8").splitlines() if registry.exists() else []
        if path not in known:
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text("\n".join(known + [path]) + "\n", encoding="utf-8")
    except OSError:
        pass


def files_under(path: pathlib.Path) -> list:
    return [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())


def digest(path: pathlib.Path) -> str:
    """sha256 of a file, or of the sorted (name, sha256) pairs of a folder."""
    if path.is_file():
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    return sha256_bytes("".join(f"{p.relative_to(path)}:{digest(p)}\n" for p in files_under(path)).encode("utf-8"))


def entries(ledger: Ledger) -> dict:
    """{path: {mode, sha256}} of what is protected and not released."""
    out = {}
    for e in ledger.entries():
        if e.get("kind") == "protect":
            for item in e.get("files") or [{"path": p} for p in e.get("paths") or []]:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    out[item["path"]] = item
        elif e.get("kind") == "release":
            for p in e.get("paths") or []:
                out.pop(p, None)
    return out


def lock(path: pathlib.Path) -> None:
    for p in files_under(path) if path.is_dir() else []:  # files first, while the folder still opens
        os.chmod(p, LOCKED)
    os.chmod(path, LOCKED)


def unlock(path: pathlib.Path, mode: int) -> None:
    os.chmod(path, mode)
    if path.is_dir():
        for p in path.rglob("*"):
            if p.is_file():
                os.chmod(p, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


@contextlib.contextmanager
def opened(paths: dict):
    """Let this process read the protected files for a moment (to check their hash), then lock them again."""
    for path, item in paths.items():
        with contextlib.suppress(OSError):
            unlock(pathlib.Path(path), item.get("mode", 0o644))
    try:
        yield
    finally:
        for path in paths:
            with contextlib.suppress(OSError):
                lock(pathlib.Path(path))


def protect(ledger: Ledger, given: list) -> tuple:
    """(newly protected items, problems) for these paths: hash, mode, then mode 000. Paths protected
    before are not hashed again (they are locked); every protected path is locked again."""
    known = entries(ledger)
    items, problems = [], []
    for raw in given:
        path = pathlib.Path(raw).resolve()
        if str(path) in known or any(i["path"] == str(path) for i in items):
            continue
        if not path.exists():
            problems.append(f"no such file: {path}")
            continue
        items.append({"path": str(path), "mode": stat.S_IMODE(path.stat().st_mode), "sha256": digest(path)})
    if problems:
        return [], problems
    if items:
        ledger.append("protect", files=items, paths=[i["path"] for i in items])
    for path in [*known, *(i["path"] for i in items)]:
        with contextlib.suppress(OSError):
            lock(pathlib.Path(path))
    register_ledger(ledger)
    return items, []


def check(ledger: Ledger, look_open: bool = False) -> list:
    """One line per protected path: missing, changed, or intact, and whether it was readable. Outside a
    look the files are locked again on the way out; during one they are readable on purpose and stay so."""
    out = []
    items = entries(ledger)
    present = {p: i for p, i in items.items() if pathlib.Path(p).exists()}
    readable = {p for p in present if os.name != "nt" and stat.S_IMODE(pathlib.Path(p).stat().st_mode)}
    with (contextlib.nullcontext() if look_open else opened(present)):
        hashes = {}
        for p in present:
            try:
                hashes[p] = digest(pathlib.Path(p))
            except OSError as e:
                hashes[p] = f"unreadable: {e}"
    for path, item in items.items():
        if path not in present:
            out.append(f"{path}: MISSING (moved or deleted after it was protected)")
            continue
        if hashes[path] != item.get("sha256"):
            out.append(f"{path}: CHANGED since it was protected" + (
                f" ({hashes[path]})" if hashes[path].startswith("unreadable") else ""))
            continue
        state = "open for the look" if look_open else (
            "was READABLE, locked again now" if path in readable else "locked")
        out.append(f"{path}: intact, sha256 {item['sha256'][:12]}, {state}")
    return out


def open_for_look(ledger: Ledger) -> None:
    for path, item in entries(ledger).items():
        with contextlib.suppress(OSError):
            unlock(pathlib.Path(path), item.get("mode", 0o644))


def close_after_look(ledger: Ledger) -> None:
    for path in entries(ledger):
        with contextlib.suppress(OSError):
            lock(pathlib.Path(path))


def release(ledger: Ledger) -> list:
    items = entries(ledger)
    for path, item in items.items():
        with contextlib.suppress(OSError):
            unlock(pathlib.Path(path), item.get("mode", 0o644))
    ledger.append("release", paths=sorted(items))
    return sorted(items)
