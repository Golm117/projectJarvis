"""The pure-logic attention core.

No module in this package reads audio, opens a socket, or calls a network — I/O
lives only in the adapter seams (``jarvis.adapters``). Anything time-dependent
takes its time source via the constructor as ``now: Callable[[], float]``; no
``time.monotonic()`` is buried inside (see ``docs/architecture/module-map.md``
§"Cross-cutting design constraints").
"""

from __future__ import annotations
