# Somame's write gate, for MicroPython (ESP32, Raspberry Pi Pico, RP2040).
#
# A microcontroller cannot host Somame: no model, no audit chain, no
# connectors. What it can do is refuse to act on its own say-so. A sensor
# or actuator that a Somame specialist drives can run this gate before it
# moves a relay, opens a valve or sends a reading out, and get the same
# answer Somame Core gives for the same action.
#
# The rules are Core's, unchanged: reads and drafts need no one; deleting,
# moving money, publishing outside and changing access always return to a
# person; anything irreversible always does; at levels 0 and 1 every other
# write asks; level 0 cannot write at all. tests/test_micropython_gate.py
# checks every combination against somame_core.gate.decide.
#
# No enum, dataclasses, typing or f-strings, so it runs on small ports.

READ = "read"
DRAFT = "draft"
CREATE = "create"
UPDATE = "update"
DELETE = "delete"
MOVE_MONEY = "move_money"
PUBLISH_EXTERNAL = "publish_external"
CHANGE_ACCESS = "change_access"
EXECUTE = "execute"

ACTIONS = (
    READ,
    DRAFT,
    CREATE,
    UPDATE,
    DELETE,
    MOVE_MONEY,
    PUBLISH_EXTERNAL,
    CHANGE_ACCESS,
    EXECUTE,
)

REVERSIBLE = "reversible"
COSTLY = "costly"
IRREVERSIBLE = "irreversible"
REVERSIBILITIES = (REVERSIBLE, COSTLY, IRREVERSIBLE)

READ_ONLY = 0
APPROVE_EVERY_WRITE = 1
NOTIFY_AND_UNDO = 2
AUTONOMOUS_AUDITED = 3

# Always returns to a person. A constant, so a device that can edit its
# settings still cannot edit this.
MANDATORY_ESCALATIONS = (DELETE, MOVE_MONEY, PUBLISH_EXTERNAL, CHANGE_ACCESS)
INERT_ACTIONS = (READ, DRAFT)


class Decision:
    __slots__ = ("allowed", "needs_human", "blocked", "rule", "undo_required", "reason")

    def __init__(self, allowed, needs_human, blocked, rule, undo_required, reason):
        self.allowed = allowed
        self.needs_human = needs_human
        self.blocked = blocked
        self.rule = rule
        self.undo_required = undo_required
        self.reason = reason

    def __repr__(self):
        return "Decision(rule=" + self.rule + ", allowed=" + str(self.allowed) + ")"


def decide(action, reversibility, level, target=None):
    """May the device act unattended, must a person see it, and why."""
    if action not in ACTIONS:
        raise ValueError("unknown action: " + str(action))
    if reversibility not in REVERSIBILITIES:
        raise ValueError("unknown reversibility: " + str(reversibility))
    level = int(level)
    what = action + (" on " + target if target else "")
    if action in INERT_ACTIONS:
        return Decision(True, False, False, "inert", False, what + " changes nothing.")
    if level <= READ_ONLY:
        return Decision(
            False, False, True, "read_only", False, "Reads only: " + what + " cannot run."
        )
    if action in MANDATORY_ESCALATIONS:
        return Decision(
            False,
            True,
            False,
            "mandatory",
            reversibility != IRREVERSIBLE,
            what + " always returns to a person.",
        )
    if reversibility == IRREVERSIBLE:
        return Decision(
            False, True, False, "irreversible", False, what + " cannot be undone: a person decides."
        )
    if level <= APPROVE_EVERY_WRITE:
        return Decision(
            False, True, False, "supervision", True, "Every write asks: " + what + " waits."
        )
    return Decision(
        True,
        False,
        False,
        "supervision",
        level == NOTIFY_AND_UNDO,
        what + " is reversible and proceeds, logged.",
    )


def requires_human(action, reversibility, level):
    if action in INERT_ACTIONS:
        return False
    if action in MANDATORY_ESCALATIONS:
        return True
    if reversibility == IRREVERSIBLE:
        return True
    return int(level) <= APPROVE_EVERY_WRITE
