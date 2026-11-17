# Somame Core

**MIT licensed.** The supervised agent runtime behind [Somame](https://somame.ai) —
the part that decides what an agent is allowed to do and what it is
allowed to claim.

[![CI](https://github.com/Akrosystems/somame-core/actions/workflows/ci.yml/badge.svg)](https://github.com/Akrosystems/somame-core/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/somame-core)](https://pypi.org/project/somame-core/)
[![Python](https://img.shields.io/pypi/pyversions/somame-core)](https://pypi.org/project/somame-core/)
[![Licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![Types](https://img.shields.io/badge/types-strict-blue)](pyproject.toml)

Somame employs AI specialists, trained to real professional standards, into a company's workforce.
The hosted platform — tenancy, billing, the workforce office, the role
catalog — is **Somame Cloud**, and it is proprietary. *This* repository
is the part that decides what an agent is allowed to do and what it is
allowed to claim, and it is open because those are the two things you
should not have to take a vendor's word for.

Somame Cloud depends on this package. Nothing here depends on Cloud, on
a database, or on any third-party runtime dependency — on purpose. The
part of a system that decides what needs a human should be auditable
without reading a dependency tree.

```bash
pip install somame-core

somame-core gate     # the full decision table, every action at every level
somame-core demo     # an agent on the most permissive setting tries to delete something
```

The name is from the Ghanaian *"soma me"* — "send me".

---

## The four ideas

Each is small enough to read in full, and each exists to prevent a
specific failure mode.

### 1. The write gate — `somame_core.gate`

Four classes of action always return to a human, at **every** supervision
level including the most autonomous one: `DELETE`, `MOVE_MONEY`,
`PUBLISH_EXTERNAL`, `CHANGE_ACCESS`. So does anything irreversible,
whatever its class.

```python
from somame_core import ActionClass, Reversibility, SupervisionLevel, decide

d = decide(ActionClass.DELETE, Reversibility.REVERSIBLE,
           SupervisionLevel.AUTONOMOUS_AUDITED, target="settlements")
d.needs_human   # True — level 3 does not exempt a deletion
d.rule          # "mandatory"
```

The set is a module-level `frozenset`, not a settings row and not a
database column, because a deployment that can edit it can defeat it.
A CI gate asserts it has not shrunk, and a second one asserts that
`gate.py` imports nothing that could read configuration.

Raising an agent's level buys speed on routine, reversible work. It buys
nothing on the four.

### 2. Provenance — `somame_core.provenance`

`DataOrigin` has five members: `MEASURED`, `RETRIEVED`, `USER_STATED`,
`MODEL_GEN`, `UNAVAILABLE`. There is deliberately **no** `SAMPLE`,
`DEMO`, `MOCK`, `PLACEHOLDER` or `ESTIMATED`. Invented data has no way
to be labelled, so it has no way to be stored and later shown as a
measurement.

`Sourced[T]` enforces two things at construction, not at display:

```python
Sourced(42.0, DataOrigin.UNAVAILABLE, reason="…")  # ProvenanceError
Sourced(1.0, DataOrigin.RETRIEVED)                 # ProvenanceError: no source named
Unavailable("No baseline is on record.")           # fine, and it says why
```

`MODEL_GEN` is storable and showable, but `is_evidence` is `False`: a
model's guess is a legitimate thing to keep, and an illegitimate thing
to put behind a reported figure.

### 3. Permission-mirrored retrieval — `somame_core.retrieval`

The failure this prevents is specific. Index a company's documents,
rank them by similarity, *then* filter by permission, and the ranking
itself has already been computed over documents the asker cannot see.
So:

* Access tags are copied onto each passage **at index time**, from the
  source system's own ACL.
* The permission test runs **inside** the search, before any score is
  kept — never over its output.
* An unresolved identity **raises**. It does not quietly fall back to
  "public only", because a silent downgrade is indistinguishable from a
  correct empty result.

```python
index.search(query, frozenset())   # IdentityUnresolved, not []
```

Mirrored ACLs older than `ACL_TTL` (one hour) raise rather than serve.
`InMemoryIndex` is a reference implementation; the tests are written
against the `RetrievalIndex` protocol so they can be pointed at a real
one.

### 4. Honest reporting — `somame_core.metrics`

```python
summarise([]).success_rate.value          # None, not 0.0
```

Three rules:

* **No completed work means no success rate.** An agent that has done
  nothing has not failed at anything.
* **Hours returned need a baseline for every task counted**, and the
  baseline must be evidence. One task without one blanks the total
  rather than understating it while looking like the total.
* **Money needs a rate the customer stated.** Value, net return and
  return on cost are arithmetic on an internal hourly cost only the
  customer knows. A rate with any origin other than `USER_STATED` is
  refused — including one this library could have inferred. There is no
  default and no estimator.

Every blank carries its reason, so a dash on a report is never
ambiguous with a bug.

## Plus: the runner and the audit chain

`somame_core.runtime` arranges things so a tool **cannot** be reached
except through the gate. The agent never holds a tool; it returns an
`Intent`, and the `Runner` classifies, gates, and only then invokes.
A `Tool` declares its `ActionClass` and `Reversibility` as part of its
identity.

A `Runner` built without an approver **holds** rather than proceeds. A
person's refusal is recorded as such (`outcome.rejected`), never as a
hold still waiting for an answer.

`AsyncRunner` is the same runner for hosts on an event loop: tools,
undo functions and the approver may be coroutines. Both runners share
one implementation of the decision path, and a test asserts they write
identical audit entries for identical intents.

`somame_core.audit` is an append-only, hash-chained log. `verify()`
says where an entry was edited, removed or reordered. A host that keeps
the chain in its own database seals each row with
`AuditEvent.chained(seq=..., prev=head_digest, ...)` and its export
still verifies with `somame-core verify`.

## somame-local: one specialist on your own machine

The same package installs `somame-local`, the free local edition: a
specialist that works in **one folder you choose**, with **your own
model**, under **this gate**. No account, no server, nothing sent
anywhere except to the model you configured, unless you choose to have
a Somame Cloud workspace oversee it (below).

```bash
pip install somame-core
export ANTHROPIC_API_KEY=...
somame-local --folder ~/books --role bookkeeper \
  --standard "ACCA Applied Skills: Financial Reporting"

# or a model you run yourself (Ollama, LM Studio, vLLM):
somame-local --folder . --base-url http://localhost:11434/v1 --model llama3.1
```

- It can list, read and write files **inside that folder only**: `..`,
  absolute paths and links that lead out are refused.
- Reads are free. At level 1 (the default) every write is shown to you
  in the terminal first; level 2 lets writes land with an undo; level 0
  blocks them.
- Commands are offered only with `--allow-commands`, and each one asks
  you at every level, because executions always escalate.
- Everything it does goes into a hash-chained audit log in
  `.somame/audit.jsonl`; `somame-local --verify` checks it, and a log
  edited by hand stops the specialist from starting.
- It works *to* the standard you name and is told it does not hold
  that qualification.

### Overseen from a Somame Cloud workspace (optional)

A company can oversee somame-local from its Somame Cloud workspace. A
manager registers the install there and gets a key, shown once:

```bash
export SOMAME_CLOUD_KEY=somame_local_...
somame-local --folder ~/books --cloud https://app.somame.example \
  --catalogue-role accounts-payable-specialist   # a role from the workspace's catalogue
```

- It runs **no higher than the level the workspace allows**, whatever
  `--level` says.
- If the workspace says so, what the gate holds is **answered by a named
  person there**, not in the terminal. Nobody answering in 15 minutes is
  a refusal, and so is a workspace that cannot be reached.
- Its **audit log is sent after every step**. The workspace checks every
  entry against the hash chain before keeping it, so a log edited,
  truncated or replaced on the machine is noticed, and the people who see
  audit there are told.
- It **fails closed**: with `--cloud`, it does not start if the workspace
  cannot be reached or does not accept the key, and it stops if the
  workspace disconnects it.

What is sent: the audit log (tools, targets, reasons and who answered,
never the contents of files read), the role, the level, the version and
the folder's *name*, never its path. An action held for a person in the
workspace is sent whole, content included, because they need to see it.
somame-local is open source and runs on your hardware, so a modified copy
could stop reporting; the workspace shows when it last heard from each
install.

## API surface

| Module | Exports | One line |
| --- | --- | --- |
| `gate` | `ActionClass`, `Reversibility`, `SupervisionLevel`, `MANDATORY_ESCALATIONS`, `INERT_ACTIONS`, `decide`, `requires_human`, `Decision` | What needs a human, and why |
| `provenance` | `DataOrigin`, `Sourced`, `Unavailable`, `ProvenanceError`, `check_no_fabrication_member` | What may be stored as a measurement |
| `runtime` | `Tool`, `Intent`, `Outcome`, `Runner`, `AsyncRunner`, `Approver`, `AsyncApprover`, `AlwaysAsk`, `DenyAll` | The supervised execution path |
| `retrieval` | `RetrievalIndex`, `InMemoryIndex`, `Chunk`, `SearchHit`, `Principal`, `resolve_principals`, `IdentityUnresolved`, `AclStale` | Search that cannot leak |
| `metrics` | `TaskRecord`, `Totals`, `Report`, `summarise`, `summarise_totals` | Figures that refuse to lie |
| `audit` | `AuditEvent`, `AuditLog`, `GENESIS`, `TamperDetected` | The record that cannot be rewritten |

## Why this is the open half

Somame's pitch is that you can hand work to an AI specialist without
handing it the ability to quietly do something you would not have
allowed, and without it reporting progress it cannot substantiate. Both
of those are claims about restraint, and a claim about restraint from a
closed-source vendor is worth very little.

So the restraint is here, readable, installable and testable on its
own, under a licence that lets you fork it if we ever weaken it.

What is **not** here, and is proprietary: the multi-tenant platform, the
billing, the orbital workforce office, the 370-role
catalog, the connector implementations, and the hosted operations.

## Repository structure

```
src/somame_core/
  gate.py         the decision function and the mandatory escalation set
  provenance.py   DataOrigin, Sourced, Unavailable
  runtime.py      Tool, Intent, Outcome, Runner, AsyncRunner
  retrieval.py    permission-mirrored search protocol and reference index
  metrics.py      summarise() and the honest-reporting rules
  audit.py        hash-chained append-only log
  cli.py          gate table, demo, verify
src/somame_local/
  workspace.py    the folder fence and the specialist's tools
  model.py        Anthropic and OpenAI-compatible models, stdlib only
  session.py      the specialist: model proposes, gate decides, you answer
  cloud.py        optional oversight from a Somame Cloud workspace
  cli.py          somame-local in the terminal
tests/            150 tests: no database, no network beyond localhost
docs/
  ARCHITECTURE.md how the modules fit together
.github/
  workflows/ci.yml  the gates, including "the escalation set has not shrunk"
```

## Development

```bash
git clone https://github.com/Akrosystems/somame-core
cd somame-core
pip install -e ".[dev]"
pytest          # no database, no network
mypy            # strict
ruff check src tests && ruff format --check src tests
```

No runtime dependencies, on purpose.

## Documentation

| Document | What it covers |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module map, the decision path, the audit chain, extension points |
| [`CHANGELOG.md`](CHANGELOG.md) | Every notable change, per release |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to propose and land changes |
| [`SECURITY.md`](SECURITY.md) | Reporting vulnerabilities |

## Status

`0.2.0` — see [CHANGELOG.md](CHANGELOG.md). The interfaces in `gate`
and `provenance` are the ones we consider settled; `runtime` and
`retrieval` may still move. Breaking changes will go through a major
version, and the mandatory escalation set will not shrink in any
version.

## Licence

MIT — see [LICENSE](LICENSE). Somame Cloud is separate and proprietary.
