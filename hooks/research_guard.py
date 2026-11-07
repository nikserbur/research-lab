#!/usr/bin/env python3
"""PreToolUse (Read, Grep, Bash, Edit, Write, MultiEdit, NotebookEdit): a readable refusal for tool calls
that name a research's holdout files while no look is open.

The protection itself is the file mode: `lab.py holdout protect PATH` records the files in the research's
LEDGER.jsonl and takes every permission away from them, so no program run as this user can open them,
a helper script or a glob included. This hook is the second layer: when a call names a protected file
(a path argument at it or inside it, a search above it while it is readable, or the file's path in a
shell command, absolute or relative to the project or the working folder), it refuses with the reason, before the call fails on permissions or, on Windows, where modes do not
protect reads, instead of succeeding. It cannot see a path a program builds at run time.

Ledgers come from <project>/research/*/ and from the list `lab.py holdout protect` keeps in the data
directory. Any error lets the call through and says so on stderr: a broken ledger must not stop all work.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import sys

from _common import emit, home, read_payload, segments

TOOLS = {"Read", "Grep", "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"}
KINDS = re.compile(r'"kind": "(protect|release|holdout_open|holdout_close)"')


def ledgers(root: pathlib.Path) -> set:
    found = set()
    try:
        found |= {p.resolve() for p in root.glob("research/*/LEDGER.jsonl")}
    except OSError:
        pass
    registry = home() / "protected-ledgers.txt"
    try:
        found |= {pathlib.Path(line.strip()) for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        pass
    return {p for p in found if p.is_file()}  # a research deleted since is no reason to warn on every call


def protected(ledger: pathlib.Path) -> tuple:
    """(protected paths not released, a look open?) from the lines that matter; others are not parsed."""
    paths, opens, closes = {}, 0, 0
    with open(ledger, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not KINDS.search(line):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            kind = e.get("kind")
            if kind == "protect":
                for p in e.get("paths") or []:
                    if isinstance(p, str) and "\x00" not in p:
                        paths[p] = True
            elif kind == "release":
                for p in e.get("paths") or []:
                    if isinstance(p, str):
                        paths.pop(p, None)
            opens += kind == "holdout_open"
            closes += kind == "holdout_close"
    return [pathlib.Path(p) for p in paths], opens > closes


def in_shell(command: str, path: pathlib.Path, root: pathlib.Path, cwd: pathlib.Path) -> bool:
    """The command names the file by a path: absolute, or relative to the project or to the folder the
    command runs in. A bare file name is not enough: data/raw/prices.csv and data/holdout/prices.csv
    share one, and refusing the simulation data would stop the research (the file mode guards the rest)."""
    names = {str(path)}
    if str(path).startswith("/private/"):  # macOS: /tmp and /var are links into /private
        names.add(str(path)[len("/private"):])
    here, folders = cwd, []
    for tokens in segments(command):  # `cd data/holdout && cat prices.csv` names it from there
        if tokens[0] == "cd" and len(tokens) > 1:
            here = (here / os.path.expanduser(tokens[1])).resolve()
            folders.append(here)
    for base in (root, root.resolve(), cwd, cwd.resolve(), *folders):
        try:
            rel = path.relative_to(base.resolve())
        except ValueError:
            continue
        names.add(rel.as_posix())
        names.add(str(base / rel))
    return any(re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w-]|\.\w)", command) for n in names)


def touches(tool: str, args: dict, cwd: pathlib.Path, root: pathlib.Path, path: pathlib.Path) -> bool:
    if tool == "Bash":
        return in_shell(str(args.get("command") or ""), path, root, cwd)
    for key in ("file_path", "path", "notebook_path"):
        value = args.get(key)
        if not isinstance(value, str) or not value or "\x00" in value:
            continue
        target = pathlib.Path(os.path.expanduser(value))
        target = (target if target.is_absolute() else cwd / target).resolve()
        if target == path or path in target.parents:
            return True
        if target in path.parents and not locked(path):  # a search passing by: a locked file cannot be read
            return True
    return False


def locked(path: pathlib.Path) -> bool:
    try:
        return os.name != "nt" and not stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False


def main() -> int:
    payload = read_payload()
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input")
    if tool not in TOOLS or not isinstance(args, dict):
        return 0
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    cwd = pathlib.Path(payload.get("cwd") or root)
    for ledger in sorted(ledgers(root)):
        try:
            paths, look_open = protected(ledger)
            if look_open:
                continue
            hit = next((p for p in paths if touches(tool, args, cwd, root, p)), None)
        except Exception as e:  # noqa: BLE001 - fail open, but loudly
            print(f"research_guard: skipped {ledger}: {e}", file=sys.stderr)
            continue
        if hit is not None:
            return emit({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{hit} is the holdout of the research in {ledger.parent}: nobody reads, searches, copies or "
                    "edits it until a look is open, and only the reviewer in mode `verify` opens one "
                    "(`lab.py holdout open`). Work on the simulation data; `lab.py holdout status` gives its hash. "
                    "If you read it by accident, say so to the controller."),
            }})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - a guard that crashes must not take every tool call with it
        print(f"research_guard: {e}", file=sys.stderr)
        sys.exit(0)
