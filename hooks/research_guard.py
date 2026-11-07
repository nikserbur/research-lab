#!/usr/bin/env python3
"""PreToolUse (Read, Grep, Glob, Bash, Edit, Write, MultiEdit, NotebookEdit): keep everyone away from a
research's holdout data until a look at it is open.

A research protects its holdout files with `lab.py holdout --lab research/<slug> protect PATH ...`, which
records them in its LEDGER.jsonl. This hook reads the ledgers of the project's research folders and denies
a tool call that touches a protected file, unless that research has a look open (`lab.py holdout open`,
which only the reviewer in mode `verify` does). In a real research a source scout read the holdout through a
helper script: instructions alone did not keep it out. Standard library only; silent when nothing is
protected.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

from _common import emit, read_payload

TOOLS = {"Read", "Grep", "Glob", "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def protected(root: pathlib.Path) -> list:
    """(research folder name, protected path, look open?) for every research of the project."""
    out = []
    try:
        ledgers = list(root.glob("research/*/LEDGER.jsonl"))
    except OSError:
        return out
    for ledger in ledgers:
        paths, opens, closes = set(), 0, 0
        try:
            lines = ledger.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            kind = e.get("kind")
            if kind == "protect":
                paths |= {p for p in e.get("paths") or [] if isinstance(p, str)}
            opens += kind == "holdout_open"
            closes += kind == "holdout_close"
        out += [(ledger.parent.name, pathlib.Path(p), opens > closes) for p in sorted(paths)]
    return out


def touches(tool: str, args: dict, cwd: pathlib.Path, path: pathlib.Path) -> bool:
    """Whether this call reads or writes the protected file: a path argument at it or above it, or a shell
    command that names it."""
    if tool == "Bash":
        command = str(args.get("command") or "")
        return path.name in command and re.search(rf"(?<![\w.-]){re.escape(path.name)}(?![\w.-])", command) is not None
    for key in ("file_path", "path", "notebook_path"):
        value = args.get(key)
        if not isinstance(value, str) or not value:
            continue
        target = pathlib.Path(os.path.expanduser(value))
        target = (target if target.is_absolute() else cwd / target).resolve()
        if target == path or target in path.parents:
            return True
    if tool == "Glob":
        pattern = str(args.get("pattern") or "")
        return path.name in pattern or ("**" in pattern and str(args.get("path") or cwd) in str(path))
    return False


def main() -> int:
    payload = read_payload()
    tool = payload.get("tool_name") or ""
    if tool not in TOOLS:
        return 0
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    cwd = pathlib.Path(payload.get("cwd") or root)
    args = payload.get("tool_input") or {}
    for research, path, look_open in protected(root):
        if not look_open and touches(tool, args, cwd, path):
            return emit({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{path} is the holdout of research/{research}: nobody reads, searches, copies or edits it until "
                    f"a look is open, and only the reviewer in mode `verify` opens one (`lab.py holdout open`). "
                    "Work on the simulation data instead; if you read it by accident, say so to the controller."),
            }})
    return 0


if __name__ == "__main__":
    sys.exit(main())
