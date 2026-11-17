"""A workspace's role catalogue, kept on this machine.

``somame-local --cloud ... --roles`` downloads the catalogue of the Somame
Cloud workspace the install belongs to, and ``--catalogue-role`` then
takes a role (its title and the standard it works to) from that copy,
without reaching Cloud. The catalogue is the workspace's, fetched with its
key; it is not part of this open-source package.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def home() -> Path:
    """Where somame-local keeps what is not tied to one folder."""
    return Path(os.environ.get("SOMAME_HOME") or Path.home() / ".somame")


def _path() -> Path:
    return home() / "catalogue.json"


def keep(roles: list[dict[str, Any]], workspace: str) -> Path:
    """Save the catalogue (or merge one role into it)."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    held = {r["slug"]: r for r in load()}
    held.update({r["slug"]: r for r in roles})
    path.write_text(
        json.dumps(
            {
                "workspace": workspace,
                "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "roles": sorted(held.values(), key=lambda r: str(r["slug"])),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        roles = json.loads(path.read_text(encoding="utf-8")).get("roles", [])
    except (ValueError, AttributeError):
        return []
    return [r for r in roles if isinstance(r, dict) and r.get("slug")]


def find(slug: str) -> dict[str, Any] | None:
    return next((r for r in load() if r["slug"] == slug), None)
