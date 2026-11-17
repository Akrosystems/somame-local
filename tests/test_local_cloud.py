"""somame-local overseen from a Somame Cloud workspace, against a small
fake of the workspace's API served over real HTTP on localhost."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from somame_core import GENESIS, AuditEvent, AuditLog, Decision, Intent, SupervisionLevel
from somame_local.cli import main
from somame_local.cloud import Cloud, CloudError, Disconnected
from somame_local.model import Call, Reply, ToolSpec, Turn
from somame_local.session import Specialist

KEY = "somame_local_test-key"
PREFIX = "/api/v1/local-installs"


class Workspace:
    """What Somame Cloud holds for one install, and how it answers."""

    def __init__(self) -> None:
        self.max_level = 2
        self.approvals_in_cloud = False
        self.disconnected = False
        self.entries: list[dict[str, Any]] = []
        self.head = GENESIS
        self.broken = ""
        self.approvals: dict[str, dict[str, Any]] = {}
        self.polls = 0
        #: What a person in the workspace answers, on the poll it is given.
        self.answer: Callable[[int], tuple[str, str]] = lambda poll: ("pending", "")
        self.fail_reports = 0
        self.bodies: list[tuple[str, str, Any]] = []
        self.url = ""

    def handle(self, method: str, path: str, key: str, body: Any) -> tuple[int, Any]:
        self.bodies.append((method, path, body))
        if key != KEY or self.disconnected:
            return 401, {"detail": "This install was disconnected from its Somame Cloud workspace."}
        route = path.removeprefix(PREFIX)
        if route == "/me/policy":
            return 200, {
                "workspace": "Acme Ltd",
                "install": "Front office laptop",
                "max_level": self.max_level,
                "approvals_in_cloud": self.approvals_in_cloud,
                "entries": len(self.entries),
                "head": self.head,
                "chain_intact": not self.broken,
            }
        if route == "/me/audit":
            if self.fail_reports:
                self.fail_reports -= 1
                return 503, {"detail": "Down for a moment."}
            return 200, self._report(body["entries"])
        if route == "/me/approvals":
            if not self.approvals_in_cloud:
                return 409, {"detail": "Answered at the machine."}
            approval_id = f"a{len(self.approvals) + 1}"
            self.approvals[approval_id] = body
            return 201, {"id": approval_id, "state": "pending", "decided_by_name": ""}
        if route.startswith("/me/approvals/"):
            self.polls += 1
            state, who = self.answer(self.polls)
            return 200, {"id": route.rsplit("/", 1)[1], "state": state, "decided_by_name": who}
        if route == "/me/roles":
            return 200, [
                {"slug": "bookkeeper", "title": "Bookkeeper", "standard": "AAT Level 3"},
                {"slug": "auditor", "title": "Auditor", "standard": "ACCA"},
            ]
        if route == "/me/roles/bookkeeper":
            return 200, {"slug": "bookkeeper", "title": "Bookkeeper", "standard": "AAT Level 3"}
        return 404, {"detail": "No such role in the catalog."}

    def _report(self, sent: list[dict[str, Any]]) -> dict[str, Any]:
        received = 0
        for raw in sent:
            if self.broken:
                break
            event = AuditEvent(**raw)
            if event.seq < len(self.entries):
                if self.entries[event.seq]["digest"] != event.digest:
                    self.broken = f"Entry {event.seq} differs from the copy Somame Cloud holds."
                continue
            if event.prev != self.head or event.compute_digest() != event.digest:
                self.broken = f"Entry {event.seq} does not reproduce."
                continue
            self.entries.append(raw)
            self.head = event.digest
            received += 1
        return {
            "received": received,
            "entries": len(self.entries),
            "head": self.head,
            "chain_intact": not self.broken,
            "chain_error": self.broken,
        }


@pytest.fixture
def workspace() -> Iterator[Workspace]:
    state = Workspace()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            status, out = state.handle(
                method, self.path, self.headers.get("X-Install-Key", ""), body
            )
            data = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


class Scripted:
    def __init__(self, replies: list[Reply]) -> None:
        self.replies = replies

    def complete(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Reply:
        return self.replies.pop(0)


def write(path: str, content: str) -> Reply:
    return Reply("", [Call("c1", "write_file", {"path": path, "content": content})])


def _never(intent: Intent, decision: Decision) -> bool:
    raise AssertionError("asked in the terminal")


def overseen(
    folder: Path,
    workspace: Workspace,
    replies: list[Reply],
    *,
    level: int = 1,
    ask: Callable[[Intent, Decision], bool] = _never,
    notices: list[str] | None = None,
    **cloud: Any,
) -> Specialist:
    told = notices if notices is not None else []
    return Specialist(
        Scripted(replies),
        folder,
        role="bookkeeper",
        level=SupervisionLevel(level),
        ask=ask,
        cloud=Cloud(workspace.url, KEY, sleep=lambda seconds: None, **cloud),
        notice=told.append,
    )


def test_the_workspace_caps_the_level_and_keeps_the_log(
    tmp_path: Path, workspace: Workspace
) -> None:
    workspace.max_level = 0
    notices: list[str] = []
    worker = overseen(
        tmp_path,
        workspace,
        [write("summary.txt", "Q3"), Reply("I can only describe it.")],
        notices=notices,
    )
    assert worker.level is SupervisionLevel.READ_ONLY
    assert "level 0 at most" in notices[0]
    worker.handle("Write the summary")
    assert not (tmp_path / "summary.txt").exists()

    # The workspace holds exactly the chain the machine wrote.
    assert [e["digest"] for e in workspace.entries] == [e.digest for e in worker.runner.log]
    AuditLog([AuditEvent(**e) for e in workspace.entries]).verify()
    [policy] = [b for m, p, b in workspace.bodies if p.endswith("/me/policy")]
    assert policy["folder"] == tmp_path.name and policy["role"] == "bookkeeper"
    # The folder's name goes, never its path.
    assert str(tmp_path) not in json.dumps([b for _, _, b in workspace.bodies])


def test_held_actions_are_answered_by_a_person_in_the_workspace(
    tmp_path: Path, workspace: Workspace
) -> None:
    workspace.approvals_in_cloud = True
    workspace.answer = lambda poll: ("approved", "Ada Lovelace") if poll >= 2 else ("pending", "")
    notices: list[str] = []
    worker = overseen(
        tmp_path, workspace, [write("summary.txt", "Q3: up 8%"), Reply("Done.")], notices=notices
    )
    assert worker.handle("Write the summary") == "Done."
    assert (tmp_path / "summary.txt").read_text() == "Q3: up 8%"
    assert any("Waiting for someone in Acme Ltd" in n for n in notices)
    [held] = workspace.approvals.values()
    assert held["tool"] == "write_file" and held["args"]["content"] == "Q3: up 8%"
    actors = [e.actor for e in worker.runner.log if e.kind == "approval"]
    assert actors == ["user:Ada Lovelace in Somame Cloud"]


def test_a_refusal_or_no_answer_in_time_changes_nothing(
    tmp_path: Path, workspace: Workspace
) -> None:
    workspace.approvals_in_cloud = True
    workspace.answer = lambda poll: ("refused", "Ada Lovelace")
    worker = overseen(tmp_path, workspace, [write("a.txt", "1"), Reply("Refused.")])
    worker.handle("Write a")
    assert not (tmp_path / "a.txt").exists()
    assert [e.actor for e in worker.runner.log if e.kind == "rejection"] == [
        "user:Ada Lovelace in Somame Cloud"
    ]

    workspace.answer = lambda poll: ("pending", "")
    worker = overseen(tmp_path, workspace, [write("b.txt", "2"), Reply("Nobody.")], wait_seconds=0)
    worker.handle("Write b")
    assert not (tmp_path / "b.txt").exists()
    rejected = [e.actor for e in worker.runner.log if e.kind == "rejection"]
    assert rejected[-1] == "user:nobody in Somame Cloud answered in time"


def test_a_log_unsent_while_unreachable_is_sent_later(tmp_path: Path, workspace: Workspace) -> None:
    workspace.fail_reports = 1
    notices: list[str] = []
    worker = overseen(
        tmp_path,
        workspace,
        [Reply("", [Call("r1", "list_files", {"path": "."})]), Reply("Empty."), Reply("Still.")],
        notices=notices,
    )
    worker.handle("What is here?")
    assert len([n for n in notices if "sent when Somame Cloud can be reached" in n]) == 1
    assert 0 < len(workspace.entries) == len(worker.runner.log)
    worker.handle("And now?")
    assert len(workspace.entries) == len(worker.runner.log)


def test_a_replaced_log_is_noticed_by_the_workspace(tmp_path: Path, workspace: Workspace) -> None:
    worker = overseen(tmp_path, workspace, [write("a.txt", "1"), Reply("ok")], level=2)
    worker.handle("Write a")
    held = len(workspace.entries)
    assert held > 0
    (tmp_path / ".somame" / "audit.jsonl").unlink()

    notices: list[str] = []
    worker = overseen(
        tmp_path, workspace, [write("b.txt", "2"), Reply("ok")], level=2, notices=notices
    )
    worker.handle("Write b")
    assert "differs from the copy" in workspace.broken
    assert len(workspace.entries) == held
    assert len([n for n in notices if "no longer accepts" in n]) == 1


def test_a_disconnected_install_stops(tmp_path: Path, workspace: Workspace) -> None:
    worker = overseen(
        tmp_path, workspace, [Reply("", [Call("r1", "list_files", {"path": "."})]), Reply("ok")]
    )
    workspace.disconnected = True
    with pytest.raises(Disconnected, match="disconnected"):
        worker.handle("What is here?")
    with pytest.raises(Disconnected):
        overseen(tmp_path, workspace, [])


def test_the_command_line_takes_a_catalogue_role_and_fails_closed(
    tmp_path: Path,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def leave(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", leave)
    monkeypatch.setenv("SOMAME_HOME", str(tmp_path / "home"))
    common = ["--folder", str(tmp_path), "--base-url", "http://127.0.0.1:9/v1", "--model", "m"]
    monkeypatch.setenv("SOMAME_CLOUD_KEY", KEY)
    args = [*common, "--cloud", workspace.url, "--catalogue-role", "bookkeeper", "--level", "2"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "A Bookkeeper is working" in out and "at level 2" in out
    assert "Overseen by Acme Ltd in Somame Cloud" in out

    # The role it took is kept on this machine, so it works offline too.
    assert main([*common, "--catalogue-role", "bookkeeper"]) == 0
    assert "A Bookkeeper is working" in capsys.readouterr().out
    assert main([*common, "--catalogue-role", "auditor"]) == 2
    assert "--roles" in capsys.readouterr().err
    monkeypatch.setenv("SOMAME_CLOUD_KEY", "somame_local_wrong")
    assert main([*common, "--cloud", workspace.url]) == 2
    assert "disconnected" in capsys.readouterr().err
    monkeypatch.delenv("SOMAME_CLOUD_KEY")
    assert main([*common, "--cloud", workspace.url]) == 2
    assert "SOMAME_CLOUD_KEY" in capsys.readouterr().err


def test_the_workspace_is_reached_over_https() -> None:
    with pytest.raises(CloudError, match="https"):
        Cloud("http://app.somame.example", KEY)
    Cloud("https://app.somame.example", KEY)
    Cloud("http://localhost:8000", KEY)


def test_the_catalogue_is_kept_for_offline_use(
    tmp_path: Path,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from somame_local import roles

    monkeypatch.setenv("SOMAME_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SOMAME_CLOUD_KEY", KEY)
    assert main(["--cloud", workspace.url, "--roles"]) == 0
    assert "Kept 2 roles from Acme Ltd's catalogue" in capsys.readouterr().out
    assert roles.find("auditor") == {"slug": "auditor", "title": "Auditor", "standard": "ACCA"}
    assert main(["--roles"]) == 2
    assert "add --cloud" in capsys.readouterr().err
