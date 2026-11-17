"""`somame-local` — a supervised specialist in your terminal.

    somame-local --folder ~/books --role "bookkeeper" \\
                 --standard "ACCA Applied Skills: Financial Reporting"

    # a model you run yourself, through Ollama:
    somame-local --folder . --base-url http://localhost:11434/v1 --model llama3.1

    somame-local --folder ~/books --verify     # check the audit log

    # overseen from a Somame Cloud workspace, with a role from its catalogue:
    export SOMAME_CLOUD_KEY=somame_local_...
    somame-local --folder ~/books --cloud https://app.somame.example \\
                 --catalogue-role accounts-payable-specialist

Your key is read from ``ANTHROPIC_API_KEY`` (or ``OPENAI_API_KEY`` for an
OpenAI-compatible server that needs one). Type a request; when the
specialist wants to change something, you are shown exactly what and
asked. ``exit`` or Ctrl-D ends the session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from somame_core import AuditLog, Decision, Intent, SupervisionLevel, TamperDetected

from . import __version__
from .cloud import Cloud, CloudError, Disconnected
from .model import DEFAULT_ANTHROPIC_MODEL, Anthropic, Model, ModelError, OpenAICompatible
from .session import Specialist
from .workspace import PRIVATE


def _ask_in_terminal(intent: Intent, decision: Decision) -> bool:
    print(f"\n  The specialist wants to {intent.tool}: {intent.target or ''}")
    print(f"  Why it needs you: {decision.reason}")
    shown = {k: v for k, v in intent.args.items()}
    if "content" in shown:
        content = str(shown.pop("content"))
        print("  New content:\n" + "\n".join(f"    {line}" for line in content.splitlines()[:40]))
        if content.count("\n") >= 40:
            print("    …")
    if shown:
        print(f"  Details: {json.dumps(shown)}")
    try:
        answer = input("  Allow it? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _notice(message: str) -> None:
    print(f"  {message}")


def _model(args: argparse.Namespace) -> Model:
    if args.base_url:
        return OpenAICompatible(args.base_url, args.model, os.environ.get("OPENAI_API_KEY", ""))
    return Anthropic(os.environ.get("ANTHROPIC_API_KEY", ""), args.model or DEFAULT_ANTHROPIC_MODEL)


def _verify(folder: Path) -> int:
    path = folder.resolve() / PRIVATE / "audit.jsonl"
    if not path.exists():
        print("No audit log yet: the specialist has not done anything in this folder.")
        return 0
    log = AuditLog.from_jsonl(path.read_text(encoding="utf-8"))
    try:
        log.verify()
    except TamperDetected as exc:
        print(f"The audit log does not reproduce: {exc}")
        return 1
    print(f"The audit log reproduces: {len(log)} entries, head {log.head[:16]}…")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="somame-local", description=__doc__.split("\n\n")[0])
    parser.add_argument("--folder", type=Path, default=Path.cwd(), help="Where it works.")
    parser.add_argument("--role", default="general assistant", help="What it does.")
    parser.add_argument("--standard", default="", help="The standard it works to.")
    parser.add_argument(
        "--level",
        type=int,
        choices=[0, 1, 2],
        default=1,
        help="0 read only, 1 every write asks you (default), 2 writes land with an undo.",
    )
    parser.add_argument("--allow-commands", action="store_true", help="Offer run_command.")
    parser.add_argument("--model", default="", help="The model's name.")
    parser.add_argument("--base-url", default="", help="An OpenAI-compatible server's address.")
    parser.add_argument("--verify", action="store_true", help="Check the audit log and stop.")
    parser.add_argument(
        "--cloud",
        default=os.environ.get("SOMAME_CLOUD_URL", ""),
        help="A Somame Cloud workspace that oversees this install (key in SOMAME_CLOUD_KEY).",
    )
    parser.add_argument(
        "--catalogue-role",
        default="",
        help="Take a role, and the standard it works to, from the workspace's catalogue.",
    )
    parser.add_argument("--version", action="version", version=f"somame-local {__version__}")
    args = parser.parse_args(argv)

    if not args.folder.is_dir():
        print(f"{args.folder} is not a folder.", file=sys.stderr)
        return 2
    if args.verify:
        return _verify(args.folder)
    if args.catalogue_role and not args.cloud:
        print("--catalogue-role takes the role from a workspace: add --cloud.", file=sys.stderr)
        return 2
    role, standard = args.role, args.standard
    cloud = None
    try:
        if args.cloud:
            # Oversight was asked for: without it, nothing starts.
            cloud = Cloud(args.cloud, os.environ.get("SOMAME_CLOUD_KEY", ""))
            if args.catalogue_role:
                role, standard = cloud.role(args.catalogue_role)
        specialist = Specialist(
            _model(args),
            args.folder,
            role=role,
            standard=standard,
            level=SupervisionLevel(args.level),
            commands=args.allow_commands,
            ask=_ask_in_terminal,
            cloud=cloud,
            notice=_notice,
        )
    except (ModelError, TamperDetected, CloudError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"A {role} is working in {specialist.workspace.folder} at level "
        f"{int(specialist.level)}. Type a request, or exit."
    )
    if cloud is not None and cloud.policy is not None:
        answered = "there" if cloud.policy.approvals_in_cloud else "here"
        print(
            f"Overseen by {cloud.policy.workspace} in Somame Cloud as "
            f"\u201c{cloud.policy.install}\u201d: its log is sent there, and what it holds "
            f"is answered {answered}."
        )
    while True:
        try:
            request = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if request.lower() in ("exit", "quit"):
            return 0
        if not request:
            continue
        try:
            print(f"\n{role}> {specialist.handle(request)}")
        except Disconnected as exc:
            print(f"\n{exc} Stopping.", file=sys.stderr)
            return 3
        except ModelError as exc:
            print(f"\n{exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
