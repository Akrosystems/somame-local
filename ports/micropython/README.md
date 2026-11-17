# The gate on a microcontroller

`somame_gate.py` is Somame Local's write gate for MicroPython: ESP32,
Raspberry Pi Pico and other RP2040 boards. A microcontroller cannot host
Somame (no model, no audit chain), but a sensor or actuator a Somame
specialist drives can run the gate itself before it acts, and refuse what
the specialist may not do unattended.

```python
import somame_gate as gate

decision = gate.decide(gate.UPDATE, gate.REVERSIBLE, gate.APPROVE_EVERY_WRITE, "valve 2")
if decision.allowed:
    open_valve(2)
elif decision.needs_human:
    ask_for_approval(decision.reason)   # over MQTT or HTTP, to your Somame
else:
    refuse(decision.reason)             # level 0: it cannot be approved into running
```

Copy the file to the board (`mpremote cp ports/micropython/somame_gate.py :`).
It uses no `enum`, `dataclasses`, `typing` or f-strings, so it runs on small
ports.

The rules are `somame_local.gate`'s, unchanged, and
`tests/test_micropython_gate.py` checks every combination of action,
reversibility and level against `somame_local.gate.decide`. The board's level is yours to set; the mandatory
set is a constant in the file, so a device whose settings can be edited
still cannot edit that.
