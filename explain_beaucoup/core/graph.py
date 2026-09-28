"""The Investigation Graph.

Traditional drill-down replaces the previous view. Here every operation adds a
node, so competing hypotheses stay alive side by side and the path back is
always visible (design plan section 6).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from ..core.context import Context
from ..core.operations import BREAKDOWN, Operation

_ids = itertools.count(1)


@dataclass
class Node:
    uid: str
    context: Context
    kind: str = BREAKDOWN
    title: str = ""
    op: Operation | None = None          # the edge that produced this node
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    pinned: bool = False
    note: str = ""
    branch: int = 0                      # which investigation path it belongs to
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def context_id(self) -> str:
        return self.context.id


class InvestigationGraph:
    """Nodes, edges, branches, pins and history for one session."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.roots: list[str] = []
        self.current: str | None = None
        self._branch_seq = itertools.count(1)

    # -- building ----------------------------------------------------------

    def add(self, context: Context, *, kind: str = BREAKDOWN,
            op: Operation | None = None, parent: str | None = None,
            title: str = "", branch: int | None = None) -> Node:
        uid = f"n{next(_ids)}"
        if branch is None:
            branch = self.nodes[parent].branch if parent in self.nodes else 0
        node = Node(uid=uid, context=context, kind=kind, op=op, parent=parent,
                    title=title or str(context), branch=branch)
        self.nodes[uid] = node
        if parent and parent in self.nodes:
            self.nodes[parent].children.append(uid)
        else:
            self.roots.append(uid)
        self.current = uid
        return node

    def branch_from(self, uid: str, context: Context, *, kind: str = BREAKDOWN,
                    op: Operation | None = None, title: str = "") -> Node:
        """Open a new analytical path *beside* the current one, rather than
        replacing it."""
        node = self.add(context, kind=kind, op=op, parent=uid, title=title,
                        branch=next(self._branch_seq))
        return node

    # -- navigation --------------------------------------------------------

    def get(self, uid: str) -> Node:
        return self.nodes[uid]

    @property
    def current_node(self) -> Node | None:
        return self.nodes.get(self.current) if self.current else None

    def goto(self, uid: str) -> Node:
        self.current = uid
        return self.nodes[uid]

    def path_to(self, uid: str) -> list[Node]:
        """Breadcrumbs: root -> ... -> node."""
        out: list[Node] = []
        cur: str | None = uid
        while cur:
            n = self.nodes[cur]
            out.append(n)
            cur = n.parent
        return list(reversed(out))

    def siblings(self, uid: str) -> list[Node]:
        n = self.nodes[uid]
        if not n.parent:
            return [self.nodes[r] for r in self.roots if r != uid]
        return [self.nodes[c] for c in self.nodes[n.parent].children if c != uid]

    def child_nodes(self, uid: str) -> list[Node]:
        """The views drilled out of this one, oldest first."""
        node = self.nodes.get(uid)
        return [self.nodes[c] for c in node.children] if node else []

    def remove(self, uid: str) -> list[str]:
        """Drop a node and everything drilled out of it.

        Closing a chart in the stack should not strand the views it spawned,
        so the whole subtree goes. Returns the uids that were removed.
        """
        node = self.nodes.get(uid)
        if node is None:
            return []
        doomed = [n.uid for n in self.walk(uid)]
        if node.parent and node.parent in self.nodes:
            self.nodes[node.parent].children.remove(uid)
        elif uid in self.roots:
            self.roots.remove(uid)
        for dead in doomed:
            self.nodes.pop(dead, None)
        if self.current in doomed:
            self.current = (node.parent if node.parent in self.nodes
                            else (self.roots[0] if self.roots else None))
        return doomed

    def walk(self, uid: str | None = None) -> Iterator[Node]:
        stack = [uid] if uid else list(self.roots)
        while stack:
            cur = stack.pop(0)
            node = self.nodes[cur]
            yield node
            stack = node.children + stack

    # -- pins --------------------------------------------------------------

    def pin(self, uid: str, note: str = "") -> Node:
        n = self.nodes[uid]
        n.pinned = True
        if note:
            n.note = note
        return n

    def unpin(self, uid: str) -> Node:
        n = self.nodes[uid]
        n.pinned = False
        return n

    @property
    def pins(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.pinned]

    @property
    def branches(self) -> dict[int, list[Node]]:
        out: dict[int, list[Node]] = {}
        for n in self.nodes.values():
            out.setdefault(n.branch, []).append(n)
        return out

    # -- export ------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"uid": n.uid, "context_id": n.context_id, "title": n.title,
                 "kind": n.kind, "parent": n.parent, "branch": n.branch,
                 "pinned": n.pinned, "note": n.note,
                 "op": n.op.verb if n.op else None,
                 "op_params": n.op.param_map if n.op else {},
                 "created_at": n.created_at,
                 "context": n.context.to_dict()}
                for n in self.nodes.values()],
            "roots": self.roots,
            "current": self.current,
        }
