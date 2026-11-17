"""Optional oversight from a Somame Cloud workspace.

Start somame-local with ``--cloud`` and the key a manager of the workspace
registered (in ``SOMAME_CLOUD_KEY``, never on the command line), and:

* it runs **no higher than the supervision level** the workspace allows;
* what the gate holds can be **answered by a named person** in the
  workspace instead of in this terminal, if the workspace says so;
* its **audit log is sent** to the workspace, which checks every entry
  against the hash chain before keeping it, so a log changed here is
  noticed there.

What is sent: the audit log (tools, targets, reasons, who approved; never
the contents of files read), the role, the level, the version and the
folder's *name*, never its path. An action held for a person in the
workspace is sent whole, content included, because they need to see it to
answer.

Without ``--cloud`` nothing is sent anywhere. With it, somame-local fails
closed: it does not start if the workspace cannot be reached or refuses its
key, and a held action nobody answers in time is refused.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from somame_core import GENESIS, AuditLog, Decision, Intent

from . import __version__

#: How long a held action waits for an answer from the workspace. Somame
#: Cloud stops waiting at the same time.
WAIT_SECONDS = 15 * 60
POLL_SECONDS = 3.0
#: Entries sent per request.
BATCH = 200


class CloudError(RuntimeError):
    """Somame Cloud could not be reached, or refused the request."""


class Disconnected(CloudError):
    """The workspace does not accept this install's key (any more)."""


@dataclass(frozen=True, slots=True)
class Policy:
    workspace: str
    install: str
    max_level: int
    approvals_in_cloud: bool


def _message(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read() or b"{}")
    except ValueError:
        return ""
    if isinstance(body, dict):
        found = body.get("detail") or body.get("message") or ""
        return found if isinstance(found, str) else json.dumps(found)
    return ""


class Cloud:
    """The workspace that oversees this install."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        wait_seconds: float = WAIT_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 30.0,
    ) -> None:
        if not key:
            raise CloudError(
                "Set SOMAME_CLOUD_KEY to the key your workspace registered for this install."
            )
        parts = urlsplit(url.strip())
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise CloudError("Somame Cloud is reached over https.")
        self.api = f"{url.strip().rstrip('/')}/api/v1/local-installs"
        self.key = key
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds
        self.sleep, self.clock, self.timeout = sleep, clock, timeout
        self.about: dict[str, Any] = {"version": __version__}
        self.policy: Policy | None = None
        #: How many entries of the log the workspace holds, and the last one.
        self.held: int | None = None
        self.head = GENESIS
        #: Set when the workspace says the log no longer reproduces.
        self.broken: str | None = None

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        request = urllib.request.Request(
            self.api + path,
            data=None if body is None else json.dumps(body, default=str).encode(),
            method=method,
            headers={
                "X-Install-Key": self.key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"somame-local/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:  # the scheme was checked
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            detail = _message(exc)
            if exc.code == 401:
                raise Disconnected(
                    detail or "Somame Cloud does not accept this install's key."
                ) from exc
            raise CloudError(f"Somame Cloud refused the request ({exc.code}). {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise CloudError(f"Could not reach Somame Cloud: {exc}") from exc

    def connect(self, about: dict[str, Any]) -> Policy:
        """Say who this install is and read what the workspace allows."""
        self.about = {"version": __version__, **about}
        body = self._call("POST", "/me/policy", self.about)
        self.policy = Policy(
            workspace=str(body["workspace"]),
            install=str(body["install"]),
            max_level=int(body["max_level"]),
            approvals_in_cloud=bool(body["approvals_in_cloud"]),
        )
        self.held, self.head = int(body["entries"]), str(body["head"])
        if not body.get("chain_intact", True):
            self.broken = "The workspace already found this install's log does not reproduce."
        return self.policy

    def roles(self) -> list[dict[str, Any]]:
        """The workspace's whole catalogue, to keep on this machine."""
        body = self._call("GET", "/me/roles")
        return [dict(r) for r in body] if isinstance(body, list) else []

    def role(self, slug: str) -> tuple[str, str]:
        """A role from the workspace's catalogue: its title and the standard
        it works to."""
        body = self._call("GET", f"/me/roles/{quote(slug, safe='')}")
        return str(body["title"]), str(body["standard"])

    def _continues(self, log: AuditLog) -> bool:
        """Whether this log starts with what the workspace already holds."""
        assert self.held is not None
        if self.held > len(log):
            return False
        return self.held == 0 or log[self.held - 1].digest == self.head

    def report(self, log: AuditLog, level: int | None = None) -> None:
        """Send what the workspace does not hold yet. A log that does not
        continue what it holds is sent from the start, so the workspace
        sees where it differs."""
        if level is not None:
            self.about["level"] = level
        if self.held is None:
            self.connect(self.about)
        events = list(log)
        start = self.held if self._continues(log) else 0
        assert start is not None
        try:
            while start < len(events):
                batch = events[start : start + BATCH]
                body = self._call(
                    "POST",
                    "/me/audit",
                    {"about": self.about, "entries": [asdict(e) for e in batch]},
                )
                self.held, self.head = int(body["entries"]), str(body["head"])
                if not body["chain_intact"]:
                    self.broken = str(body["chain_error"])
                    return
                start += len(batch)
        except CloudError:
            self.held = None  # read where the workspace is before the next try
            raise

    def ask(self, intent: Intent, decision: Decision) -> tuple[bool, str]:
        """Hold the action for a person in the workspace. Refused if nobody
        answers in time, or if the workspace cannot be reached."""
        try:
            held = self._call(
                "POST",
                "/me/approvals",
                {
                    "tool": intent.tool,
                    "target": intent.target or "",
                    "reason": decision.reason,
                    "args": intent.args,
                },
            )
        except Disconnected:
            return False, "nobody: this install was disconnected from Somame Cloud"
        except CloudError:
            return False, "nobody: Somame Cloud could not be reached"
        deadline = self.clock() + self.wait_seconds
        while True:
            try:
                answer = self._call("GET", f"/me/approvals/{held['id']}")
            except Disconnected:
                return False, "nobody: this install was disconnected from Somame Cloud"
            except CloudError:
                answer = {"state": "pending"}  # try again until the deadline
            state = answer["state"]
            who = f"{answer.get('decided_by_name') or 'someone'} in Somame Cloud"
            if state == "approved":
                return True, who
            if state == "refused":
                return False, who
            if state == "expired" or self.clock() >= deadline:
                return False, "nobody in Somame Cloud answered in time"
            self.sleep(self.poll_seconds)
