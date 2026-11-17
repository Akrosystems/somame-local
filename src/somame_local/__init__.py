"""Somame Local: one supervised specialist, on your own machine.

The free edition of Somame, under the MIT licence. A specialist works in one
folder you choose, with your own model (an Anthropic key, or a model you run
yourself through an OpenAI-compatible server such as Ollama), under the same
rule as Somame Cloud. No account, no server, nothing sent anywhere but to the
model you configured.

* Reads are free. Every write waits for you in the terminal (at level 1,
  the default), or lands with an undo (level 2). Level 0 blocks writes.
* Commands run only if you start it with ``--allow-commands``, and each
  one asks you first, at every level, because the gate escalates
  executions.
* Everything it does is written to a hash-chained audit log in
  ``.somame/audit.jsonl`` inside the folder, which ``--verify`` checks.
* ``--serve`` opens the web office for everyone in the room.
* Optionally, a Somame Cloud workspace oversees it (``--cloud``): the
  level it may run at, approvals answered by a named person there, and
  its audit log checked against the hash chain. See
  :mod:`somame_local.cloud`.

The rules themselves are the part worth reading with suspicion, and each is
one short module with no dependencies:

* :mod:`.gate` decides which actions return to a person. Deleting, paying,
  publishing outside and changing access always do, at every level;
* :mod:`.runtime` makes the gate unavoidable: the model never holds a tool;
* :mod:`.provenance` makes a figure without a source unstorable;
* :mod:`.retrieval` binds who may read a passage to the passage;
* :mod:`.metrics` leaves a blank rather than invent a number;
* :mod:`.audit` chains every action, so a rewritten entry shows.
  ``somame-local verify <file>`` checks a chain, including one exported
  from Somame Cloud.

Nothing here needs more than the standard library.
"""

from .audit import GENESIS, AuditEvent, AuditLog, TamperDetected
from .gate import (
    INERT_ACTIONS,
    MANDATORY_ESCALATIONS,
    ActionClass,
    Decision,
    GateError,
    Reversibility,
    SupervisionLevel,
    decide,
    requires_human,
)
from .metrics import Report, TaskRecord, Totals, summarise, summarise_totals
from .provenance import (
    DataOrigin,
    ProvenanceError,
    Sourced,
    Unavailable,
    check_no_fabrication_member,
)
from .retrieval import (
    ACL_TTL,
    AclStale,
    Chunk,
    IdentityUnresolved,
    InMemoryIndex,
    Principal,
    RetrievalIndex,
    SearchHit,
    resolve_principals,
)
from .runtime import (
    AlwaysAsk,
    Approver,
    AsyncApprover,
    AsyncRunner,
    DenyAll,
    Intent,
    Outcome,
    Runner,
    Tool,
    ToolNotRegistered,
)

__version__ = "0.2.0"

__all__ = [
    "ACL_TTL",
    "GENESIS",
    "INERT_ACTIONS",
    "MANDATORY_ESCALATIONS",
    "AclStale",
    "ActionClass",
    "AlwaysAsk",
    "Approver",
    "AsyncApprover",
    "AsyncRunner",
    "AuditEvent",
    "AuditLog",
    "Chunk",
    "DataOrigin",
    "Decision",
    "DenyAll",
    "GateError",
    "IdentityUnresolved",
    "InMemoryIndex",
    "Intent",
    "Outcome",
    "Principal",
    "ProvenanceError",
    "Report",
    "RetrievalIndex",
    "Reversibility",
    "Runner",
    "SearchHit",
    "Sourced",
    "SupervisionLevel",
    "TamperDetected",
    "TaskRecord",
    "Tool",
    "ToolNotRegistered",
    "Totals",
    "Unavailable",
    "__version__",
    "check_no_fabrication_member",
    "decide",
    "requires_human",
    "resolve_principals",
    "summarise",
    "summarise_totals",
]
