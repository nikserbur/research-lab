"""Keeping the holdout unread.

`lab.py holdout protect PATH` records every file under PATH (hash, mode, size, modification time) and
takes every permission away from it (mode 000), so no program run as this user can open it: not a tool
call, not a helper script, not a glob, a copy or a link. A folder keeps its own mode, so its listing
shows names but no contents; symlinks are skipped, since they may point outside. Nothing here opens a
protected file outside a look: `status` compares sizes and times, and the hashes are checked when the
reviewer opens a look. `open` gives the permissions back for that look, `close` takes them away again,
`release` returns them for good. A file found readable outside a look is locked again and recorded as
exposed, and a look on an exposed file is refused: that holdout is spent. Every chmod is checked, so
a lock that did not take is never reported as one. The research_guard hook adds a readable refusal.

What it cannot stop: a chmod and a read before anything checks, a copy made before `protect`, a
process that already had the file open, readers during an open look, or Windows, where a mode only
sets the read-only flag.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import stat
import subprocess

from .ledger import Ledger, sha256_bytes

LOCKED = 0o000
POSIX = os.name != "nt"


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
    """The regular files a protected path stands for, without following symlinks."""
    if path.is_symlink():
        return []
    if path.is_file():
        return [path]
    out = []
    for folder, _, names in os.walk(path):
        out += [p for p in (pathlib.Path(folder) / n for n in names) if p.is_file() and not p.is_symlink()]
    return sorted(out)


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def member(path: pathlib.Path) -> dict:
    st = path.stat()
    return {"path": str(path), "mode": stat.S_IMODE(st.st_mode), "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "sha256": digest(path)}


def members(item: dict) -> list:
    """The files of one protected path; an entry written by 0.11.0 lists none and stands for itself."""
    return item.get("members") or [{"path": item["path"], "mode": item.get("mode", 0o644), "sha256": item.get("sha256")}]


def entries(ledger: Ledger) -> dict:
    """{path: item} of what is protected and not released."""
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


def all_members(ledger: Ledger) -> list:
    return [m for item in entries(ledger).values() for m in members(item)]


def look_open(ledger: Ledger) -> bool:
    return len(ledger.find("holdout_open")) > len(ledger.find("holdout_close"))


def set_mode(path: str, mode: int) -> bool:
    """chmod, and check that it took: a file that cannot be locked must never be reported as locked."""
    try:
        os.chmod(path, mode)
        return not POSIX or stat.S_IMODE(os.stat(path).st_mode) == mode
    except OSError:
        return False


def lock(ms: list) -> list:
    return [m["path"] for m in ms if not set_mode(m["path"], LOCKED)]


def unlock(ms: list) -> list:
    return [m["path"] for m in ms if not set_mode(m["path"], m.get("mode") or 0o644)]


def readable(m: dict) -> bool:
    try:
        return POSIX and bool(stat.S_IMODE(os.stat(m["path"]).st_mode))
    except OSError:
        return False


def git_state(path: pathlib.Path) -> tuple:
    """(problem, warning): a tracked holdout has readable copies in every checkout and worktree; an
    untracked one that git does not ignore makes `git add -A` fail once it has no permissions."""
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(path.parent), *args], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None

    tracked = git("ls-files", "--", path.name)
    if tracked is None or tracked.returncode != 0:
        return None, None  # no git, or not a repository
    if tracked.stdout.strip():
        return (f"{path} is tracked by git, so every checkout and worktree has a readable copy: "
                f"`git rm -r --cached` it and list it in .gitignore first"), None
    ignored = git("check-ignore", "-q", "--", path.name)
    if ignored is not None and ignored.returncode == 1:
        return None, f"{path} is not in .gitignore: without permissions it makes `git add -A` fail"
    return None, None


def protect(ledger: Ledger, given: list) -> tuple:
    """(newly protected items, problems, warnings). All or nothing: if a file cannot be locked, none is,
    and nothing is recorded."""
    known = entries(ledger)
    taken = {m["path"] for item in known.values() for m in members(item)}
    items, problems, warnings = [], [], []
    for raw in given:
        root = pathlib.Path(raw).resolve()
        if str(root) in known or any(i["path"] == str(root) for i in items):
            continue
        if not root.exists():
            problems.append(f"no such file: {root}")
            continue
        files = files_under(root)
        if not files:
            problems.append(f"no files under {root} (symlinks are not followed)")
            continue
        if any(str(f) in taken for f in files):
            problems.append(f"{root}: protected already, under another path")
            continue
        problem, warning = git_state(root)
        if problem:
            problems.append(problem)
            continue
        warnings += [warning] if warning else []
        try:
            ms = [member(f) for f in files]
        except OSError as e:
            problems.append(f"cannot read {e.filename}: {e.strerror} (locked by another research?)")
            continue
        whole = ms[0]["sha256"] if root.is_file() else sha256_bytes(
            "".join(f"{pathlib.Path(m['path']).relative_to(root)}:{m['sha256']}\n" for m in ms).encode("utf-8"))
        items.append({"path": str(root), "sha256": whole, "members": ms})
    if problems:
        return [], problems, []
    new = [m for item in items for m in members(item)]
    failed = lock(new)
    if failed:
        unlock(new)
        return [], [f"could not take the permissions from {p} (not your file, or a read-only file system)"
                    for p in failed], []
    if items:
        ledger.append("protect", files=items, paths=[i["path"] for i in items])
    register_ledger(ledger)
    return items, [], warnings


def enforce(ledger: Ledger, by: str) -> tuple:
    """(exposed, failed): lock again every protected file that is readable outside a look, and record it
    as exposed; a look on it is refused from then on."""
    if look_open(ledger):
        return [], []
    exposed = [m for m in all_members(ledger) if readable(m)]
    if not exposed:
        return [], []
    failed = lock(exposed)
    ledger.append("holdout_exposed", paths=[m["path"] for m in exposed], by=by, unlocked=failed)
    return [m["path"] for m in exposed], failed


def exposures(ledger: Ledger) -> dict:
    """{file: how}: every protected file that was readable outside a look, found so or released before one."""
    out = {}
    for e in ledger.entries():
        when = str(e.get("ts", ""))[:19]
        if e.get("kind") == "holdout_exposed":
            for p in e.get("paths") or []:
                out.setdefault(p, f"found readable by {e.get('by')} at {when}")
        elif e.get("kind") == "release" and e.get("forced"):
            for p in e.get("members") or e.get("paths") or []:
                out.setdefault(p, f"released before any look at {when}")
    return out


def changed(m: dict) -> bool:
    if "size" not in m:
        return False
    try:
        st = os.stat(m["path"])
    except OSError:
        return False
    return st.st_size != m["size"] or st.st_mtime_ns != m["mtime_ns"]


def check(ledger: Ledger) -> list:
    """One line per protected path, without opening a file: how many files, locked or readable, and
    what is MISSING, CHANGED (size or time), added since, or EXPOSED."""
    out, gone, is_open = [], exposures(ledger), look_open(ledger)
    for root, item in entries(ledger).items():
        ms = members(item)
        missing = {m["path"] for m in ms if not os.path.lexists(m["path"])}
        moved = [m for m in ms if m["path"] not in missing and changed(m)]
        open_ = [m for m in ms if m["path"] not in missing and readable(m)]
        added = (sorted({str(p) for p in files_under(pathlib.Path(root))} - {m["path"] for m in ms})
                 if pathlib.Path(root).is_dir() else [])
        spent = [m["path"] for m in ms if m["path"] in gone]
        problems = ([f"{len(missing)} MISSING"] if missing else []) + \
                   ([f"{len(moved)} CHANGED (size or time)"] if moved else []) + \
                   ([f"{len(added)} added since, not protected"] if added else []) + \
                   ([f"EXPOSED: {gone[spent[0]]}" + (f" (and {len(spent) - 1} more)" if len(spent) > 1 else "")]
                    if spent else [])
        state = "open for the look" if is_open else (f"{len(open_)} READABLE" if open_ else "locked")
        out.append(f"{root}: {len(ms)} file(s), sha256 {str(item.get('sha256', '?'))[:12]}, {state}"
                   + ("; " + "; ".join(problems) if problems else ", intact"))
    return out


def open_for_look(ledger: Ledger) -> list:
    """Give the protected files their permissions back and check their hashes; returns the problems."""
    ms = all_members(ledger)
    failed = set(unlock(ms))
    problems = [f"{p}: could not give the permissions back" for p in sorted(failed)]
    for m in ms:
        if m["path"] in failed or not m.get("sha256") or not pathlib.Path(m["path"]).is_file():
            continue
        try:
            if digest(pathlib.Path(m["path"])) != m["sha256"]:
                problems.append(f"{m['path']} CHANGED since it was protected")
        except OSError as e:
            problems.append(f"{m['path']}: {e.strerror}")
    return problems


def close_after_look(ledger: Ledger) -> list:
    return lock(all_members(ledger))


def release(ledger: Ledger, forced: bool) -> tuple:
    """(released paths, files that kept no permissions)."""
    items = entries(ledger)
    ms = [m for item in items.values() for m in members(item)]
    failed = unlock(ms)
    ledger.append("release", paths=sorted(items), members=[m["path"] for m in ms], forced=forced)
    return sorted(items), failed
