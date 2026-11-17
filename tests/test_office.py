"""The local web office: requests from a browser, approvals answered by a
named person in the room, the token on every call, and the conversation
kept in SQLite (or PostgreSQL)."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from somame_core import Decision, Intent, SupervisionLevel
from somame_local.model import Call, Reply, ToolSpec, Turn
from somame_local.office import Office, office_token, serve
from somame_local.session import Specialist
from somame_local.store import SqliteStore, Store, StoreError, open_store


class Scripted:
    def __init__(self, replies: list[Reply]) -> None:
        self.replies = replies

    def complete(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Reply:
        return self.replies.pop(0)


def write(path: str, content: str) -> Reply:
    return Reply("", [Call("c1", "write_file", {"path": path, "content": content})])


class Running:
    def __init__(self, office: Office, url: str) -> None:
        self.office, self.url = office, url

    def call(
        self, path: str, body: dict[str, Any] | None = None, token: str | None = None
    ) -> tuple[int, Any]:
        request = urllib.request.Request(
            self.url + path,
            data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "POST",
            headers={
                "X-Office-Token": self.office.token if token is None else token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def until(self, check: Callable[[], Any], seconds: float = 10) -> Any:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            found = check()
            if found:
                return found
            time.sleep(0.05)
        raise AssertionError("waited too long")


@pytest.fixture
def office_for(tmp_path: Path) -> Iterator[Callable[..., Running]]:
    opened: list[tuple[Any, Office, Store]] = []

    def start(
        replies: list[Reply], *, wait_seconds: float = 10, store: Store | None = None
    ) -> Running:
        held = store or SqliteStore(tmp_path / ".somame" / "office.db", tmp_path.name)

        def make(ask: Any, notice: Any) -> Specialist:
            return Specialist(
                Scripted(replies),
                tmp_path,
                role="bookkeeper",
                standard="AAT Level 3",
                level=SupervisionLevel.APPROVE_EVERY_WRITE,
                ask=ask,
                notice=notice,
            )

        office = Office(make, held, office_token(tmp_path), wait_seconds=wait_seconds)
        server = serve(office, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        opened.append((server, office, held))
        return Running(office, f"http://127.0.0.1:{server.server_address[1]}")

    yield start
    for server, office, held in opened:
        server.shutdown()
        server.server_close()
        office.close()
        held.close()


def test_a_request_from_the_browser_waits_for_a_named_person(
    tmp_path: Path, office_for: Callable[..., Running]
) -> None:
    running = office_for([write("summary.txt", "Q3: up 8%"), Reply("Written.")])
    status, body = running.call("/api/status")
    assert status == 200
    assert (body["role"], body["level"], body["folder"]) == ("bookkeeper", 1, tmp_path.name)

    assert running.call("/api/requests", {"text": "Write the summary", "by": "Ama"})[0] == 202
    held = running.until(
        lambda: [a for a in running.call("/api/approvals")[1] if a["state"] == "pending"]
    )
    assert held[0]["tool"] == "write_file" and held[0]["args"]["content"] == "Q3: up 8%"
    assert not (tmp_path / "summary.txt").exists()

    ok, _ = running.call(f"/api/approvals/{held[0]['id']}", {"approve": True, "by": "Kofi"})
    assert ok == 200
    again, _ = running.call(f"/api/approvals/{held[0]['id']}", {"approve": False, "by": "Ama"})
    assert again == 409  # the first answer counts
    said = running.until(
        lambda: [m for m in running.call("/api/messages")[1] if m["role"] == "specialist"]
    )
    assert said[0]["text"] == "Written."
    assert (tmp_path / "summary.txt").read_text() == "Q3: up 8%"
    audit = running.call("/api/audit")[1]
    assert any(e["actor"] == "user:Kofi (local office)" for e in audit)
    [answered] = running.call("/api/approvals")[1]
    assert (answered["state"], answered["answered_by"]) == ("approved", "Kofi")


def test_nobody_answering_in_time_is_a_refusal(
    tmp_path: Path, office_for: Callable[..., Running]
) -> None:
    running = office_for([write("a.txt", "1"), Reply("Not allowed.")], wait_seconds=0.2)
    running.call("/api/requests", {"text": "Write a", "by": "Ama"})
    running.until(
        lambda: [m for m in running.call("/api/messages")[1] if m["role"] == "specialist"]
    )
    assert not (tmp_path / "a.txt").exists()
    [expired] = running.call("/api/approvals")[1]
    assert expired["state"] == "expired"


def test_every_call_needs_the_token_and_the_page_is_locked_down(
    tmp_path: Path, office_for: Callable[..., Running]
) -> None:
    running = office_for([])
    assert running.call("/api/status", token="")[0] == 401
    assert running.call("/api/status", token="x" * 43)[0] == 401
    assert running.call("/api/requests", {"text": "hi", "by": "Ama"}, token="")[0] == 401
    assert running.call("/api/requests", {"text": "hi"})[0] == 400  # who is asking
    with urllib.request.urlopen(running.url + "/", timeout=10) as page:
        assert "default-src 'none'" in page.headers["Content-Security-Policy"]
        assert page.headers["X-Frame-Options"] == "DENY"
        assert b"office.js" in page.read()
    # The token is kept, private to the folder's owner, and the same next time.
    token = (tmp_path / ".somame" / "office.token").read_text().strip()
    assert token == running.office.token == office_token(tmp_path)
    if os.name == "posix":
        assert (tmp_path / ".somame" / "office.token").stat().st_mode & 0o077 == 0


def test_the_conversation_survives_a_restart(
    tmp_path: Path, office_for: Callable[..., Running]
) -> None:
    first = office_for([Reply("Hello Ama.")])
    first.call("/api/requests", {"text": "Hello", "by": "Ama"})
    first.until(lambda: [m for m in first.call("/api/messages")[1] if m["role"] == "specialist"])
    second = office_for([])
    texts = [m["text"] for m in second.call("/api/messages")[1]]
    assert texts == ["Hello", "Hello Ama."]


def test_held_actions_left_by_a_stopped_office_expire(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "office.db", "books")
    left = store.add_approval("write_file", "a.txt", "Every write asks.", {"path": "a.txt"})

    def make(ask: Any, notice: Any) -> Specialist:
        return Specialist(Scripted([]), tmp_path, ask=ask, notice=notice)

    office = Office(make, store, "t" * 43)
    office.close()
    [row] = store.approvals()
    assert (row["id"], row["state"]) == (left, "expired")
    store.close()


def test_postgres_keeps_offices_apart() -> None:
    url = os.environ.get("SOMAME_TEST_POSTGRES")
    if not url:
        pytest.skip("Set SOMAME_TEST_POSTGRES to a PostgreSQL address to run this.")
    books = open_store(url, Path("."), "books-test")
    clinic = open_store(url, Path("."), "clinic-test")
    try:
        # Rows from an earlier run stay: read only what this run adds.
        start = max([m["id"] for m in books.messages(limit=100000)] or [0])
        books.add_message("person", "Ama", "Reconcile October")
        clinic.add_message("person", "Dr Mensah", "Summarise the rota")
        assert [m["text"] for m in books.messages(after=start)] == ["Reconcile October"]
        approval = books.add_approval("write_file", "a.txt", "asks", {"path": "a.txt"})
        books.answer_approval(approval, "approved", "Kofi")
        assert books.approvals("approved")[0]["answered_by"] == "Kofi"
    finally:
        books.close()
        clinic.close()


def test_only_postgres_addresses_are_taken(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="postgresql://"):
        open_store("mysql://x", tmp_path, "books")
    assert isinstance(open_store("", tmp_path, "books"), SqliteStore)


def test_an_ask_that_names_who_answered_is_recorded(tmp_path: Path) -> None:
    def ask(intent: Intent, decision: Decision) -> tuple[bool, str]:
        return True, "Ama"

    worker = Specialist(Scripted([write("b.txt", "2"), Reply("ok")]), tmp_path, ask=ask)
    worker.handle("Write b")
    assert [e.actor for e in worker.runner.log if e.kind == "approval"] == ["user:Ama"]
