"""somame-local: the folder fence, the gate in the terminal, the audit log."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from somame_local import Decision, Intent, SupervisionLevel, TamperDetected
from somame_local.cli import main
from somame_local.model import Call, OpenAICompatible, Reply, ToolSpec, Turn
from somame_local.session import Specialist
from somame_local.workspace import OutsideFolder, Workspace


class Scripted:
    """A model that answers from a list, and remembers what it was shown."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies = replies
        self.seen: list[list[Turn]] = []

    def complete(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Reply:
        self.seen.append(list(turns))
        return self.replies.pop(0)


def write(path: str, content: str) -> Reply:
    return Reply("", [Call("c1", "write_file", {"path": path, "content": content})])


def specialist(
    tmp_path: Path, replies: list[Reply], answers: list[bool], **extra: Any
) -> tuple[Specialist, list[Intent]]:
    asked: list[Intent] = []

    def ask(intent: Intent, decision: Decision) -> bool:
        asked.append(intent)
        return answers.pop(0)

    return Specialist(Scripted(replies), tmp_path, role="bookkeeper", ask=ask, **extra), asked


def test_the_folder_is_a_fence(tmp_path: Path) -> None:
    (tmp_path / "ledger.csv").write_text("a,b\n")
    space = Workspace(tmp_path)
    assert space.read_file({"path": "ledger.csv"})["content"] == "a,b\n"
    for escape in ("../secret.txt", "/etc/passwd", ".somame/audit.jsonl"):
        with pytest.raises(OutsideFolder):
            space.path(escape)
    assert [f["path"] for f in space.list_files({"path": "."})["files"]] == ["ledger.csv"]


def test_a_write_waits_for_you_and_a_refusal_changes_nothing(tmp_path: Path) -> None:
    replies = [write("summary.txt", "Q3: up 8%"), Reply("I was not allowed to write it.")]
    worker, asked = specialist(tmp_path, replies, answers=[False])
    assert worker.handle("Summarise Q3 into summary.txt") == "I was not allowed to write it."
    assert [i.tool for i in asked] == ["write_file"]
    assert not (tmp_path / "summary.txt").exists()
    # The model was told it was refused.
    [refused] = worker.model.seen[1][-1]["results"]  # type: ignore[attr-defined]
    assert "refused" in refused["content"]

    replies = [write("summary.txt", "Q3: up 8%"), Reply("Done.")]
    worker, _ = specialist(tmp_path, replies, answers=[True])
    assert worker.handle("Try again") == "Done."
    assert (tmp_path / "summary.txt").read_text() == "Q3: up 8%"


def test_reads_never_ask_and_level_zero_blocks_writes(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello")
    replies = [
        Reply("", [Call("r1", "read_file", {"path": "notes.md"})]),
        write("notes.md", "changed"),
        Reply("I can only describe the change."),
    ]
    worker, asked = specialist(tmp_path, replies, answers=[], level=SupervisionLevel.READ_ONLY)
    worker.handle("Fix the notes")
    assert asked == []  # nothing was put to the person: the read is free, the write is blocked
    assert (tmp_path / "notes.md").read_text() == "hello"


def test_commands_are_offered_only_when_allowed_and_always_ask(tmp_path: Path) -> None:
    worker, _ = specialist(tmp_path, [], answers=[])
    assert "run_command" not in {s.name for s in worker.specs}
    run = Reply("", [Call("x1", "run_command", {"command": "echo hi"})])
    worker, asked = specialist(
        tmp_path,
        [run, Reply("ok")],
        answers=[True],
        commands=True,
        level=SupervisionLevel.NOTIFY_AND_UNDO,
    )
    worker.handle("Say hi")
    assert [i.tool for i in asked] == ["run_command"]  # asked even at level 2
    assert "hi" in worker.outcomes[0].result["output"]


def test_the_audit_log_is_kept_and_an_edit_is_caught(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worker, _ = specialist(tmp_path, [write("a.txt", "1"), Reply("ok")], answers=[True])
    worker.handle("Write a")
    log = tmp_path / ".somame" / "audit.jsonl"
    assert log.exists()
    assert main(["--folder", str(tmp_path), "--verify"]) == 0
    assert "reproduces" in capsys.readouterr().out

    log.write_text(log.read_text().replace("write_file", "read_file"))
    assert main(["--folder", str(tmp_path), "--verify"]) == 1
    with pytest.raises(TamperDetected):
        specialist(tmp_path, [], answers=[])


def test_an_openai_compatible_server_gets_the_same_conversation() -> None:
    model = OpenAICompatible("http://localhost:11434/v1", "llama3.1")
    turns: list[Turn] = [
        {"role": "user", "text": "Read it"},
        {"role": "assistant", "text": "", "calls": [Call("c9", "read_file", {"path": "a"})]},
        {"role": "tool", "results": [{"id": "c9", "name": "read_file", "content": "{}"}]},
    ]
    messages = model._messages("system", turns)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool"]
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[3]["tool_call_id"] == "c9"
