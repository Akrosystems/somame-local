"""somame-local — one supervised specialist, on your own machine.

The free local edition of Somame: a specialist that works in one folder
you choose, with your own model (an Anthropic key, or a model you run
yourself through an OpenAI-compatible server such as Ollama), under the
same gate as Somame Cloud. No account, no server, nothing sent anywhere
but to the model you configured.

* Reads are free. Every write waits for you in the terminal (at level 1,
  the default), or lands with an undo (level 2). Level 0 blocks writes.
* Commands run only if you start it with ``--allow-commands``, and each
  one asks you first, at every level, because Core escalates executions.
* Everything it does is written to a hash-chained audit log in
  ``.somame/audit.jsonl`` inside the folder, which ``--verify`` checks.

Like Somame Core, it has no dependencies beyond the standard library.
"""

__version__ = "0.1.0"
