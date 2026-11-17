# Somame Local

**One supervised AI specialist, on your own machine. MIT licensed.**

[![CI](https://github.com/Akrosystems/somame-local/actions/workflows/ci.yml/badge.svg)](https://github.com/Akrosystems/somame-local/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/somame-local)](https://pypi.org/project/somame-local/)
[![Python](https://img.shields.io/pypi/pyversions/somame-local)](https://pypi.org/project/somame-local/)
[![Licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![Types](https://img.shields.io/badge/types-strict-blue)](pyproject.toml)

[Somame](https://somame.ai) lets a company employ AI specialists, trained
to real professional standards, and supervise them the way it supervises
people. Somame Local is the free edition: one specialist that works in
**one folder you choose**, with **your own model**, under the same rule as
the hosted platform. No account, no server, nothing sent anywhere except to
the model you configured.

```bash
pip install somame-local
export ANTHROPIC_API_KEY=...
somame-local --folder ~/books --role bookkeeper \
  --standard "ACCA Applied Skills: Financial Reporting"

# or a model you run yourself (Ollama, LM Studio, vLLM):
somame-local --folder . --base-url http://localhost:11434/v1 --model llama3.1
```

It also carries the rules it works under, each a short module with no
dependencies, so you can read exactly what it may do without you:

```bash
somame-local gate                 # the full decision table, every action at every level
somame-local demo                 # an agent on the most permissive setting tries to delete something
somame-local verify audit.jsonl   # check a hash-chained audit log, one exported from Somame Cloud too
```

The name is from the Ghanaian *"soma me"*: "send me".

---

## What the specialist can do

- List, read and write files **inside that folder only**: `..`, absolute
  paths and links that lead out are refused.
- Reads are free. At level 1 (the default) every write is shown to you in
  the terminal first; level 2 lets writes land with an undo; level 0 blocks
  them.
- Commands are offered only with `--allow-commands`, and each one asks you
  at every level, because executions always escalate.
- Everything it does goes into a hash-chained audit log in
  `.somame/audit.jsonl`; `somame-local --verify` checks it, and a log
  edited by hand stops the specialist from starting.
- It works *to* the standard you name and is told it does not hold that
  qualification.

## The web office: one specialist for everyone in the room

```bash
somame-local --folder ~/books --role bookkeeper --serve
# The bookkeeper's office is open at http://127.0.0.1:8765/?token=...
```

The same specialist, in a browser. People type requests with their name;
the specialist works through them one at a time, in the folder, under the
same gate. What it holds appears for whoever has the page open, and the
first answer, with that person's name, is what the audit log records.
Nobody answering in 15 minutes is a refusal.

- Every call needs the office's token, kept in `.somame/office.token`
  (readable only by you). The page sets a strict Content-Security-Policy
  and loads nothing from outside the machine.
- It listens on this machine only, unless `--host` says otherwise.
- The conversation and the approvals are kept in SQLite in the folder's
  `.somame`, or, for one database across several offices on a home server,
  in PostgreSQL: `pip install "somame-local[postgres]"` and
  `--database postgresql://...`.

## On a Raspberry Pi

```bash
sh install/raspberry-pi.sh ~/office "bookkeeper"
```

installs the web office as a service on a Raspberry Pi 4 (4 GB or more) or
any Debian or Ubuntu machine. Your model settings go in
`~/.somame/office.env`, readable only by you; with
[Ollama](https://ollama.com) on the same machine, nothing leaves the
building.

## On a microcontroller

A microcontroller cannot host a specialist, but a sensor or actuator a
specialist drives can run the gate itself before it acts:
[`ports/micropython/somame_gate.py`](ports/micropython/README.md) decides
exactly as `somame_local.gate.decide` does, which a test checks for every
combination.

## Overseen from a Somame Cloud workspace (optional)

A company can oversee Somame Local from its Somame Cloud workspace. A
manager registers the install there and gets a key, shown once:

```bash
export SOMAME_CLOUD_KEY=somame_local_...
somame-local --folder ~/books --cloud https://app.somame.example \
  --catalogue-role accounts-payable-specialist   # a role from the workspace's catalogue
```

- It runs **no higher than the level the workspace allows**, whatever
  `--level` says.
- If the workspace says so, what the gate holds is **answered by a named
  person there**, not in the terminal. Nobody answering in 15 minutes is a
  refusal, and so is a workspace that cannot be reached.
- Its **audit log is sent after every step**. The workspace checks every
  entry against the hash chain before keeping it, so a log edited,
  truncated or replaced on the machine is noticed, and the people who see
  audit there are told.
- It **fails closed**: with `--cloud`, it does not start if the workspace
  cannot be reached or does not accept the key, and it stops if the
  workspace disconnects it.

What is sent: the audit log (tools, targets, reasons and who answered,
never the contents of files read), the role, the level, the version and the
folder's *name*, never its path. An action held for a person in the
workspace is sent whole, content included, because they need to see it.
Somame Local is open source and runs on your hardware, so a modified copy
could stop reporting; the workspace shows when it last heard from each
install.

The workspace's role catalogue can be kept on the machine, so a role is
taken without reaching Cloud:

```bash
somame-local --cloud https://app.somame.example --roles      # once, online
somame-local --folder ~/books --catalogue-role accounts-payable-specialist
```

---

## The rules it works under

Each is small enough to read in full, and each exists to prevent a
specific failure. Somame Cloud enforces the same rules.

### 1. The write gate: `somame_local.gate`

Four classes of action always return to a human, at **every** supervision
level including the most autonomous one: `DELETE`, `MOVE_MONEY`,
`PUBLISH_EXTERNAL`, `CHANGE_ACCESS`. So does anything irreversible,
whatever its class.

```python
from somame_local import ActionClass, Reversibility, SupervisionLevel, decide

d = decide(ActionClass.DELETE, Reversibility.REVERSIBLE,
           SupervisionLevel.AUTONOMOUS_AUDITED, target="settlements")
d.needs_human   # True: level 3 does not exempt a deletion
d.rule          # "mandatory"
```

The set is a module-level `frozenset`, not a settings row and not a
database column, because a deployment that can edit it can defeat it. A CI
gate asserts it has not shrunk, and a second one asserts that `gate.py`
imports nothing that could read configuration.

Raising an agent's level buys speed on routine, reversible work. It buys
nothing on the four.

### 2. Provenance: `somame_local.provenance`

`DataOrigin` has five members: `MEASURED`, `RETRIEVED`, `USER_STATED`,
`MODEL_GEN`, `UNAVAILABLE`. There is deliberately **no** `SAMPLE`, `DEMO`,
`MOCK`, `PLACEHOLDER` or `ESTIMATED`. Invented data has no way to be
labelled, so it has no way to be stored and later shown as a measurement.

`Sourced[T]` enforces two things at construction, not at display:

```python
Sourced(42.0, DataOrigin.UNAVAILABLE, reason="…")  # ProvenanceError
Sourced(1.0, DataOrigin.RETRIEVED)                 # ProvenanceError: no source named
Unavailable("No baseline is on record.")           # fine, and it says why
```

`MODEL_GEN` is storable and showable, but `is_evidence` is `False`: a
model's guess is a legitimate thing to keep, and an illegitimate thing to
put behind a reported figure.

### 3. Permission-mirrored retrieval: `somame_local.retrieval`

Index a company's documents, rank them by similarity, *then* filter by
permission, and the ranking itself has already been computed over
documents the asker cannot see. So:

* Access tags are copied onto each passage **at index time**, from the
  source system's own ACL.
* The permission test runs **inside** the search, before any score is
  kept, never over its output.
* An unresolved identity **raises**. It does not quietly fall back to
  "public only", because a silent downgrade is indistinguishable from a
  correct empty result.

```python
index.search(query, frozenset())   # IdentityUnresolved, not []
```

Mirrored ACLs older than `ACL_TTL` (one hour) raise rather than serve.
`InMemoryIndex` is a reference implementation; the tests are written
against the `RetrievalIndex` protocol so they can be pointed at a real one.

### 4. Honest reporting: `somame_local.metrics`

```python
summarise([]).success_rate.value          # None, not 0.0
```

* **No completed work means no success rate.** An agent that has done
  nothing has not failed at anything.
* **Hours returned need a baseline for every task counted**, and the
  baseline must be evidence. One task without one blanks the total rather
  than understating it while looking like the total.
* **Money needs a rate the customer stated.** A rate with any origin other
  than `USER_STATED` is refused, including one this library could have
  inferred. There is no default and no estimator.

Every blank carries its reason, so a dash on a report is never ambiguous
with a bug.

### The runner and the audit chain

`somame_local.runtime` arranges things so a tool **cannot** be reached
except through the gate. The agent never holds a tool; it returns an
`Intent`, and the `Runner` classifies, gates, and only then invokes. A
`Tool` declares its `ActionClass` and `Reversibility` as part of its
identity.

A `Runner` built without an approver **holds** rather than proceeds. A
person's refusal is recorded as such (`outcome.rejected`), never as a hold
still waiting for an answer. `AsyncRunner` is the same runner for hosts on
an event loop; a test asserts both write identical audit entries for
identical intents.

`somame_local.audit` is an append-only, hash-chained log. `verify()` says
where an entry was edited, removed or reordered. Somame Cloud seals its
own audit log the same way, so a workspace's export verifies with
`somame-local verify`, without trusting the platform that wrote it.

## API surface

| Module | Exports | One line |
| --- | --- | --- |
| `gate` | `ActionClass`, `Reversibility`, `SupervisionLevel`, `MANDATORY_ESCALATIONS`, `INERT_ACTIONS`, `decide`, `requires_human`, `Decision` | What needs a human, and why |
| `provenance` | `DataOrigin`, `Sourced`, `Unavailable`, `ProvenanceError`, `check_no_fabrication_member` | What may be stored as a measurement |
| `runtime` | `Tool`, `Intent`, `Outcome`, `Runner`, `AsyncRunner`, `Approver`, `AsyncApprover`, `AlwaysAsk`, `DenyAll` | The supervised execution path |
| `retrieval` | `RetrievalIndex`, `InMemoryIndex`, `Chunk`, `SearchHit`, `Principal`, `resolve_principals`, `IdentityUnresolved`, `AclStale` | Search that cannot leak |
| `metrics` | `TaskRecord`, `Totals`, `Report`, `summarise`, `summarise_totals` | Figures that refuse to lie |
| `audit` | `AuditEvent`, `AuditLog`, `GENESIS`, `TamperDetected` | The record that cannot be rewritten |

All of it is importable from `somame_local` directly.

## Why this half is open

Somame's promise is that you can hand work to an AI specialist without
handing it the ability to quietly do something you would not have allowed,
and without it reporting progress it cannot substantiate. Both are claims
about restraint, and a claim about restraint from a closed-source vendor is
worth very little. So the restraint is here, readable, installable and
testable on its own, under a licence that lets you fork it if we ever
weaken it.

What is **not** here, and is proprietary: the multi-tenant platform, the
billing, the workforce office, the 370-role catalogue, the connectors and
the hosted operations. That is Somame Cloud.

## Repository structure

```
src/somame_local/
  gate.py         the decision function and the mandatory escalation set
  provenance.py   DataOrigin, Sourced, Unavailable
  runtime.py      Tool, Intent, Outcome, Runner, AsyncRunner
  retrieval.py    permission-mirrored search protocol and reference index
  metrics.py      summarise() and the honest-reporting rules
  audit.py        hash-chained append-only log
  checks.py       somame-local gate, demo and verify
  workspace.py    the folder fence and the specialist's tools
  model.py        Anthropic and OpenAI-compatible models, stdlib only
  session.py      the specialist: model proposes, gate decides, you answer
  cloud.py        optional oversight from a Somame Cloud workspace
  office.py       the web office: the same specialist in a browser
  store.py        the office's conversation and approvals: SQLite or PostgreSQL
  roles.py        the workspace's catalogue, kept on this machine
  cli.py          somame-local in the terminal
install/
  raspberry-pi.sh the web office as a service on a Pi or any Debian machine
ports/micropython/
  somame_gate.py  the gate on a microcontroller, decided exactly as gate.py decides
tests/            165 tests: no network beyond localhost; PostgreSQL when given one
docs/
  ARCHITECTURE.md how the modules fit together
.github/
  workflows/ci.yml  the gates, including "the escalation set has not shrunk"
```

## Development

```bash
git clone https://github.com/Akrosystems/somame-local
cd somame-local
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

`0.2.0`, not yet on PyPI; see [CHANGELOG.md](CHANGELOG.md). The interfaces
in `gate` and `provenance` are the ones we consider settled; `runtime` and
`retrieval` may still move. Breaking changes will go through a major
version, and the mandatory escalation set will not shrink in any version.

## Licence

MIT; see [LICENSE](LICENSE). Somame Cloud is separate and proprietary.
