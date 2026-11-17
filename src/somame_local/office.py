"""The local web office: the same specialist, in a browser.

    somame-local --folder ~/books --role bookkeeper --serve

opens ``http://127.0.0.1:8765/?token=...``. People in the office type
requests; the specialist works on them one at a time, in the folder, under
the same gate. What the gate holds is shown to whoever has the page open,
and the first answer, with the name they gave, is what the audit log
records. Nobody answering in 15 minutes is a refusal.

Everything the page does goes through a small JSON API that needs the
office's token, kept in ``.somame/office.token`` (readable only by you), so
another program on the machine, or a web page in a browser on it, cannot
drive the specialist. It listens on this machine only unless ``--host``
says otherwise. Standard library only: ``http.server`` and ``threading``.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import queue
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from somame_core import Decision, Intent

from .cloud import Disconnected
from .model import ModelError
from .session import Specialist
from .store import Store

#: How long a held action waits for someone in the office.
WAIT_SECONDS = 15 * 60
#: The largest request body the API reads.
BODY_LIMIT = 1_000_000

Answer = tuple[bool, str]
MakeSpecialist = Callable[[Callable[[Intent, Decision], Answer], Callable[[str], None]], Specialist]


def office_token(folder: Path) -> str:
    """The office's token: made once, kept in the folder's ``.somame``."""
    path = folder.resolve() / ".somame" / "office.token"
    if path.exists():
        found = path.read_text(encoding="utf-8").strip()
        if len(found) >= 32:
            return found
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):  # Windows keeps its own permissions
        os.chmod(path, 0o600)
    return token


class _Held:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.answer: Answer = (False, "nobody")


class Office:
    """The specialist, its queue of requests, and what it is holding."""

    def __init__(
        self,
        make: MakeSpecialist,
        store: Store,
        token: str,
        *,
        wait_seconds: float = WAIT_SECONDS,
    ) -> None:
        self.store = store
        self.token = token
        self.wait_seconds = wait_seconds
        self.busy = False
        self.stopped = ""
        self._held: dict[int, _Held] = {}
        self._lock = threading.Lock()
        self._requests: queue.Queue[tuple[str, str] | None] = queue.Queue()
        # Anything left pending by an office that stopped is not coming back.
        for old in store.approvals("pending"):
            store.answer_approval(int(old["id"]), "expired", "nobody: the office restarted")
        self.specialist = make(self._ask, self._notice)
        self._worker = threading.Thread(target=self._work, name="somame-office", daemon=True)
        self._worker.start()

    # --- What the specialist calls -------------------------------------

    def _notice(self, message: str) -> None:
        self.store.add_message("notice", "somame-local", message)

    def _ask(self, intent: Intent, decision: Decision) -> Answer:
        approval_id = self.store.add_approval(
            intent.tool, intent.target or "", decision.reason, dict(intent.args)
        )
        held = _Held()
        with self._lock:
            self._held[approval_id] = held
        answered = held.event.wait(self.wait_seconds)
        with self._lock:
            self._held.pop(approval_id, None)
        if not answered:
            self.store.answer_approval(approval_id, "expired", "nobody")
            return False, "nobody in the office answered in time"
        return held.answer

    # --- What people do -------------------------------------------------

    def request(self, text: str, by: str) -> None:
        self.store.add_message("person", by, text)
        self._requests.put((text, by))

    def answer(self, approval_id: int, approve: bool, by: str) -> bool:
        """The first answer counts. False if it was not waiting."""
        with self._lock:
            held = self._held.get(approval_id)
            if held is None or held.event.is_set():
                return False
            held.answer = (approve, f"{by} (local office)")
            held.event.set()
        self.store.answer_approval(approval_id, "approved" if approve else "refused", by)
        return True

    def _work(self) -> None:
        while True:
            item = self._requests.get()
            if item is None:
                return
            text, by = item
            self.busy = True
            try:
                reply = self.specialist.handle(f"(From {by}) {text}")
                self.store.add_message("specialist", self.role, reply)
            except Disconnected as exc:
                self.stopped = str(exc)
                self._notice(f"{exc} The office no longer takes requests.")
                return
            except ModelError as exc:
                self._notice(str(exc))
            except Exception as exc:  # keep the office open for the next request
                self._notice(f"That request failed: {type(exc).__name__}: {exc}")
            finally:
                self.busy = False

    @property
    def role(self) -> str:
        return self.specialist.role

    def status(self) -> dict[str, Any]:
        cloud = self.specialist.cloud
        policy = cloud.policy if cloud is not None else None
        return {
            "role": self.specialist.role,
            "standard": self.specialist.standard,
            "level": int(self.specialist.level),
            "folder": self.specialist.workspace.folder.name,
            "busy": self.busy or not self._requests.empty(),
            "stopped": self.stopped,
            "cloud": (
                {
                    "workspace": policy.workspace,
                    "install": policy.install,
                    "approvals_in_cloud": policy.approvals_in_cloud,
                }
                if policy
                else None
            ),
        }

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        events = list(self.specialist.runner.log)[-limit:]
        return [
            {"seq": e.seq, "at": e.at, "actor": e.actor, "kind": e.kind, "summary": e.summary}
            for e in reversed(events)
        ]

    def close(self) -> None:
        self._requests.put(None)


def _handler(office: Office) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "somame-local"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def _allowed(self) -> bool:
            given = self.headers.get("X-Office-Token", "")
            return hmac.compare_digest(given.encode(), office.token.encode())

        def _body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > BODY_LIMIT:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"detail": "Too large."})
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "Send JSON."})
                return None
            if not isinstance(body, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "Send a JSON object."})
                return None
            return body

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path == "/":
                self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
                return
            if parts.path == "/office.js":
                self._send(HTTPStatus.OK, SCRIPT.encode(), "text/javascript; charset=utf-8")
                return
            if parts.path == "/office.css":
                self._send(HTTPStatus.OK, STYLE.encode(), "text/css; charset=utf-8")
                return
            if not parts.path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"detail": "Not here."})
                return
            if not self._allowed():
                self._json(HTTPStatus.UNAUTHORIZED, {"detail": "This needs the office's token."})
                return
            query = parse_qs(parts.query)
            if parts.path == "/api/status":
                self._json(HTTPStatus.OK, office.status())
            elif parts.path == "/api/messages":
                after = int((query.get("after") or ["0"])[0] or 0)
                self._json(HTTPStatus.OK, office.store.messages(after=after))
            elif parts.path == "/api/approvals":
                self._json(HTTPStatus.OK, office.store.approvals(limit=50))
            elif parts.path == "/api/audit":
                self._json(HTTPStatus.OK, office.audit())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"detail": "Not here."})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if not self._allowed():
                self._json(HTTPStatus.UNAUTHORIZED, {"detail": "This needs the office's token."})
                return
            body = self._body()
            if body is None:
                return
            by = str(body.get("by") or "").strip()[:80]
            if not by:
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "Say who you are."})
                return
            if path == "/api/requests":
                text = str(body.get("text") or "").strip()
                if not text:
                    self._json(HTTPStatus.BAD_REQUEST, {"detail": "Write a request."})
                    return
                if office.stopped:
                    self._json(HTTPStatus.CONFLICT, {"detail": office.stopped})
                    return
                office.request(text[:20_000], by)
                self._json(HTTPStatus.ACCEPTED, {"queued": True})
                return
            if path.startswith("/api/approvals/"):
                try:
                    approval_id = int(path.rsplit("/", 1)[1])
                except ValueError:
                    self._json(HTTPStatus.NOT_FOUND, {"detail": "No such approval."})
                    return
                if not office.answer(approval_id, bool(body.get("approve")), by):
                    self._json(HTTPStatus.CONFLICT, {"detail": "It is no longer waiting."})
                    return
                self._json(HTTPStatus.OK, {"answered": True})
                return
            self._json(HTTPStatus.NOT_FOUND, {"detail": "Not here."})

    return Handler


def serve(office: Office, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """The server, bound and ready; call ``serve_forever`` on it."""
    server = ThreadingHTTPServer((host, port), _handler(office))
    server.daemon_threads = True
    return server


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Somame local office</title>
<link rel="stylesheet" href="/office.css">
<script src="/office.js" defer></script>
</head>
<body>
<header>
  <svg class="mark" viewBox="0 0 64 64" aria-hidden="true"><mask id="cut" maskUnits="userSpaceOnUse"
   x="0" y="0" width="64" height="64"><rect width="64" height="64" fill="#fff"/><circle cx="32" cy="32"
   r="8.2" fill="#000"/></mask><path d="M41.06 13.42A11.5 11.5 0 1 0 32 32A11.5 11.5 0 1 1 22.94 50.58"
   fill="none" stroke="currentColor" stroke-width="6.6" stroke-linecap="round" mask="url(#cut)"/>
   <circle class="gate" cx="32" cy="32" r="5.3"/><circle class="dot" cx="20.67" cy="45.5" r="3.3"/></svg>
  <div><h1 id="role">Somame local office</h1><p id="meta" class="faint"></p></div>
  <p id="state" class="chip"></p>
</header>
<main>
  <section id="locked" hidden>
    <h2>This page needs the office's link</h2>
    <p>Open the address somame-local printed when it started, with its <code>?token=</code>.
    It is also in <code>.somame/office.token</code> in the folder.</p>
  </section>
  <section id="office" hidden>
    <div id="held"></div>
    <ol id="talk"></ol>
    <form id="ask">
      <input id="who" placeholder="Your name" maxlength="80" required>
      <textarea id="text" rows="3" placeholder="What should it do?" required></textarea>
      <button type="submit">Send</button>
    </form>
    <details><summary>Audit log</summary><ol id="audit" class="audit"></ol></details>
  </section>
</main>
</body>
</html>
"""

STYLE = """:root{--void:#110f0d;--soil:#18150f;--clay:#211c16;--edge:#3a3028;--bone:#f5eee4;
--dust:#aa9c8c;--ember:#f4720b;--jade:#2fbf8f;--rust:#e0563f;--amber:#e8a317;
--body:system-ui,-apple-system,"Segoe UI",sans-serif;--mono:ui-monospace,"SF Mono",Consolas,monospace}
@media (prefers-color-scheme:light){:root{--void:#f7f2ea;--soil:#fffcf7;--clay:#fff;--edge:#d8cbbb;
--bone:#1e1813;--dust:#54493f;--ember:#ae4e07;--jade:#0f7a57;--rust:#b53a25;--amber:#8f5e00}}
*{box-sizing:border-box}body{margin:0;background:var(--void);color:var(--bone);font:15px/1.5 var(--body)}
header{display:flex;gap:14px;align-items:center;padding:16px 20px;border-bottom:1px solid var(--edge);background:var(--soil)}
h1{font-size:18px;margin:0}h2{font-size:17px}.faint{color:var(--dust);margin:0;font-size:13px}
.mark{width:32px;height:32px;flex:none;color:var(--bone)}.mark .gate{fill:var(--ember)}.mark .dot{fill:var(--jade)}
.chip{margin-left:auto;font-size:12px;padding:3px 10px;border-radius:99px;border:1px solid var(--edge)}
main{max-width:860px;margin:0 auto;padding:16px}
#talk{list-style:none;padding:0;display:flex;flex-direction:column;gap:10px}
#talk li{background:var(--clay);border:1px solid var(--edge);border-radius:10px;padding:10px 12px;white-space:pre-wrap}
#talk li.person{border-left:3px solid var(--dust)}#talk li.specialist{border-left:3px solid var(--ember)}
#talk li.notice{border-left:3px solid var(--amber);font-size:13px}
#talk b{display:block;font-size:12px;color:var(--dust);font-weight:600}
form{display:grid;gap:8px;margin-top:12px}input,textarea{width:100%;padding:9px 10px;border-radius:8px;
border:1px solid var(--edge);background:var(--soil);color:var(--bone);font:inherit}
button{padding:9px 14px;border-radius:8px;border:0;background:var(--ember);color:#190b00;font-weight:600;cursor:pointer}
button.quiet{background:transparent;color:var(--bone);border:1px solid var(--edge)}
.held{border:1px solid var(--ember);border-radius:10px;padding:12px;margin-bottom:10px;background:var(--clay)}
.held pre,code{font-family:var(--mono);font-size:12.5px}.held pre{white-space:pre-wrap;max-height:260px;overflow:auto;
background:var(--soil);padding:8px;border-radius:6px}.row{display:flex;gap:8px}
.audit{font-family:var(--mono);font-size:12px;color:var(--dust)}details{margin-top:18px}
"""

SCRIPT = r"""(() => {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get('token');
  if (fromUrl) {
    sessionStorage.setItem('somame-office', fromUrl);
    url.searchParams.delete('token');
    history.replaceState(null, '', url.pathname);
  }
  const token = sessionStorage.getItem('somame-office');
  const $ = (id) => document.getElementById(id);
  if (!token) { $('locked').hidden = false; return; }
  $('office').hidden = false;
  $('who').value = localStorage.getItem('somame-office-name') || '';
  const api = async (path, body) => {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: { 'X-Office-Token': token, 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 401) { $('office').hidden = true; $('locked').hidden = false; throw new Error('locked'); }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed');
    return data;
  };
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  let after = 0;
  const who = () => $('who').value.trim();
  async function refresh() {
    const status = await api('/api/status');
    $('role').textContent = status.role;
    $('meta').textContent = [status.standard && 'to the standard of ' + status.standard,
      'folder ' + status.folder, 'level ' + status.level,
      status.cloud && 'overseen by ' + status.cloud.workspace].filter(Boolean).join(' · ');
    $('state').textContent = status.stopped ? 'stopped' : status.busy ? 'working' : 'ready';
    for (const m of await api('/api/messages?after=' + after)) {
      const li = el('li', m.role);
      li.append(el('b', '', m.role === 'notice' ? 'Note' : m.author), document.createTextNode(m.text));
      $('talk').append(li);
      after = m.id;
      li.scrollIntoView({ block: 'end' });
    }
    const held = $('held'); held.replaceChildren();
    for (const a of (await api('/api/approvals')).filter((a) => a.state === 'pending')) {
      const box = el('div', 'held');
      box.append(el('b', '', 'It wants to ' + a.tool + (a.target ? ': ' + a.target : '')),
        el('p', 'faint', a.reason), el('pre', '', a.args.content !== undefined ? String(a.args.content) : JSON.stringify(a.args, null, 2)));
      const row = el('div', 'row');
      for (const [label, approve, cls] of [['Allow', true, ''], ['Refuse', false, 'quiet']]) {
        const b = el('button', cls, label);
        b.onclick = async () => {
          if (!who()) { $('who').focus(); return; }
          try { await api('/api/approvals/' + a.id, { approve, by: who() }); } catch (e) { alert(e.message); }
          refresh();
        };
        row.append(b);
      }
      box.append(row); held.append(box);
    }
    const audit = $('audit'); audit.replaceChildren();
    for (const e of await api('/api/audit')) audit.append(el('li', '', e.at.slice(0, 19).replace('T', ' ') + '  ' + e.actor + '  ' + e.summary));
  }
  $('ask').onsubmit = async (event) => {
    event.preventDefault();
    localStorage.setItem('somame-office-name', who());
    try { await api('/api/requests', { text: $('text').value, by: who() }); $('text').value = ''; } catch (e) { alert(e.message); }
    refresh();
  };
  refresh();
  setInterval(() => refresh().catch(() => {}), 2500);
})();
"""
