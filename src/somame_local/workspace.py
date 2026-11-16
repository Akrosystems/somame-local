"""The folder a local specialist works in, and the tools it has there.

Every path is resolved inside the folder before it is used: ``..``, an
absolute path, or a link that leads outside is refused, so the specialist
cannot read or write anything you did not put in front of it. The
``.somame`` directory, where the audit log lives, is out of its reach too.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from somame_core import ActionClass, Reversibility, Tool

#: The most a read returns, so a huge file cannot flood the model.
READ_LIMIT = 60_000
#: The most a listing returns.
LIST_LIMIT = 500
COMMAND_TIMEOUT = 300
PRIVATE = ".somame"


class OutsideFolder(PermissionError):
    """A path that does not stay inside the specialist's folder."""


class Workspace:
    def __init__(self, folder: Path) -> None:
        self.folder = folder.resolve()

    def path(self, relative: str) -> Path:
        candidate = (self.folder / relative).resolve()
        if candidate != self.folder and self.folder not in candidate.parents:
            raise OutsideFolder(f"{relative!r} is outside the folder this specialist works in.")
        inside = candidate.relative_to(self.folder)
        if inside.parts and inside.parts[0] == PRIVATE:
            raise OutsideFolder("The audit log is not the specialist's to read or change.")
        return candidate

    # --- what the tools do -------------------------------------------------

    def list_files(self, args: Mapping[str, Any]) -> dict[str, Any]:
        base = self.path(str(args.get("path") or "."))
        if not base.is_dir():
            raise FileNotFoundError(f"{args.get('path')!r} is not a folder.")
        found = []
        for item in sorted(base.rglob("*")):
            relative = item.relative_to(self.folder)
            if relative.parts[0] == PRIVATE or not item.is_file():
                continue
            found.append({"path": relative.as_posix(), "bytes": item.stat().st_size})
            if len(found) >= LIST_LIMIT:
                break
        return {"files": found}

    def read_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        target = self.path(str(args["path"]))
        text = target.read_text(encoding="utf-8", errors="replace")
        clipped = len(text) > READ_LIMIT
        return {"path": str(args["path"]), "content": text[:READ_LIMIT], "clipped": clipped}

    def write_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        target = self.path(str(args["path"]))
        previous = target.read_text(encoding="utf-8") if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args["content"]), encoding="utf-8")
        return {"path": str(args["path"]), "previous": previous}

    def undo_write(self, args: Mapping[str, Any], result: Any) -> None:
        target = self.path(str(args["path"]))
        previous = result.get("previous") if isinstance(result, dict) else None
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(previous, encoding="utf-8")

    def run_command(self, args: Mapping[str, Any]) -> dict[str, Any]:
        finished = subprocess.run(
            str(args["command"]),
            shell=True,
            cwd=self.folder,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        output = (finished.stdout or "") + (finished.stderr or "")
        return {"exit_code": finished.returncode, "output": output[-20_000:]}

    # --- as Core sees them ---------------------------------------------------

    def tools(self, *, commands: bool) -> list[Tool]:
        found = [
            Tool(
                "list_files",
                ActionClass.READ,
                Reversibility.REVERSIBLE,
                self.list_files,
                description="List the files in the folder, or in a folder inside it.",
            ),
            Tool(
                "read_file",
                ActionClass.READ,
                Reversibility.REVERSIBLE,
                self.read_file,
                description="Read one text file in the folder.",
            ),
            Tool(
                "write_file",
                ActionClass.UPDATE,
                Reversibility.REVERSIBLE,
                self.write_file,
                description="Write a text file in the folder (creating or replacing it).",
                undo=self.undo_write,
            ),
        ]
        if commands:
            found.append(
                Tool(
                    "run_command",
                    ActionClass.EXECUTE,
                    Reversibility.IRREVERSIBLE,
                    self.run_command,
                    description="Run a shell command in the folder. You are asked every time.",
                )
            )
        return found


#: What the model is told each tool takes.
SCHEMAS: dict[str, dict[str, Any]] = {
    "list_files": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "A folder inside, or '.'."}},
        "required": ["path"],
    },
    "read_file": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "The file's path in the folder."}},
        "required": ["path"],
    },
    "write_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The file's path in the folder."},
            "content": {"type": "string", "description": "The whole new content."},
        },
        "required": ["path", "content"],
    },
    "run_command": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Exactly what to run."}},
        "required": ["command"],
    },
}
