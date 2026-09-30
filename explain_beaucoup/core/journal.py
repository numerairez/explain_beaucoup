"""The investigation journal: what the analyst *did*, in the order they did it.

The InvestigationGraph is structural - it deduplicates, prunes closed
subtrees and forgets that a node was revisited. The journal is the opposite:
append-only and chronological. Returning to a chart, closing one, asking for
an explanation or being blocked by the semantic model are all moves in an
investigation, and none of them is a node.

Each entry snapshots the Context it acted on rather than pointing at a node,
so the commentary still renders after that node has been closed. Nothing
here computes or phrases anything - `engine/commentary.py` turns entries
into prose, deterministically, from the entry plus the engine.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from .context import Context

# Journal actions. Analytical verbs are recorded as OPERATION with the verb in
# `verb`; the rest are session moves that never produce a node of their own.
START = "start"
OPERATION = "operation"
RETURN = "return"
CLOSE = "close"
PIN = "pin"
UNPIN = "unpin"
EXPLAIN = "explain"
EVIDENCE = "evidence"
BLOCKED = "blocked"


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    action: str
    context: Context | None = None       # the state the move landed on
    kind: str = ""
    uid: str | None = None               # node, while it still exists
    parent: str | None = None            # that node's parent, when recorded
    verb: str = ""
    params: tuple[tuple[str, Any], ...] = ()
    source: str = ""
    prior: Context | None = None         # the state the move started from
    prior_kind: str = ""
    branch: bool = False
    reused: bool = False                 # landed on a chart already open
    title: str = ""                      # the chart's title at the time
    detail: str = ""                   # a pin note, evidence headline, reason
    count: int = 0                       # e.g. views closed with a node
    at: str = field(default_factory=lambda: datetime.now().isoformat(
        timespec="seconds"), compare=False)

    @property
    def param_map(self) -> dict[str, Any]:
        return dict(self.params)


class Journal:
    """Append-only log of one session's investigation moves."""

    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []
        self._seq = itertools.count(1)

    def record(self, action: str, **fields: Any) -> JournalEntry:
        fields["params"] = tuple(sorted(dict(fields.get("params") or {}).items()))
        entry = JournalEntry(seq=next(self._seq), action=action, **fields)
        self.entries.append(entry)
        return entry

    def __iter__(self) -> Iterator[JournalEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def to_dict(self) -> list[dict[str, Any]]:
        return [{"seq": e.seq, "action": e.action, "verb": e.verb,
                 "params": e.param_map, "source": e.source, "uid": e.uid,
                 "parent": e.parent,
                 "kind": e.kind, "branch": e.branch, "reused": e.reused,
                 "title": e.title,
                 "detail": e.detail, "count": e.count, "at": e.at,
                 "context": e.context.to_dict(with_lineage=False)
                 if e.context else None}
                for e in self.entries]
