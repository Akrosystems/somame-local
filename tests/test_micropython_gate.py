"""The MicroPython port of the gate decides exactly as Core does."""

from __future__ import annotations

import ast
import importlib.util
import itertools
from pathlib import Path
from types import ModuleType

import pytest

from somame_core import ActionClass, Reversibility, SupervisionLevel, decide, requires_human

PORT = Path(__file__).resolve().parents[1] / "ports" / "micropython" / "somame_gate.py"


def _port() -> ModuleType:
    spec = importlib.util.spec_from_file_location("somame_gate", PORT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_combination_matches_core() -> None:
    gate = _port()
    assert set(gate.ACTIONS) == {a.value for a in ActionClass}
    assert set(gate.REVERSIBILITIES) == {r.value for r in Reversibility}
    for action, reversibility, level in itertools.product(
        ActionClass, Reversibility, SupervisionLevel
    ):
        core = decide(action, reversibility, level, target="relay 1")
        port = gate.decide(action.value, reversibility.value, int(level), "relay 1")
        assert (
            port.allowed,
            port.needs_human,
            port.blocked,
            port.rule,
            port.undo_required,
        ) == (
            core.allowed,
            core.needs_human,
            core.blocked,
            core.rule,
            core.undo_required,
        ), (action, reversibility, level)
        assert gate.requires_human(action.value, reversibility.value, int(level)) == (
            requires_human(action, reversibility, level)
        )


def test_the_mandatory_set_is_cores() -> None:
    from somame_core import MANDATORY_ESCALATIONS

    assert set(_port().MANDATORY_ESCALATIONS) == {a.value for a in MANDATORY_ESCALATIONS}


def test_unknown_actions_are_refused() -> None:
    gate = _port()
    with pytest.raises(ValueError):
        gate.decide("launch", gate.REVERSIBLE, 3)
    with pytest.raises(ValueError):
        gate.decide(gate.UPDATE, "probably", 3)


def test_it_uses_nothing_small_ports_lack() -> None:
    tree = ast.parse(PORT.read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert imports == []
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]  # no f-strings
