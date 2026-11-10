---
type: "query"
date: "2026-11-09T12:49:28.867456+00:00"
question: "What connects GDK_DEBUG, NO_AT_BRIDGE, GTK_A11Y to the rest of the system?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["GDK_DEBUG"]
---

# Q: What connects GDK_DEBUG, NO_AT_BRIDGE, GTK_A11Y to the rest of the system?

## Answer

Expanded from original query via vocab: [gdk debug]. Traversed BFS: only 5 nodes - GDK_DEBUG/NO_AT_BRIDGE/GTK_A11Y all defines edges from run-tests-inner.sh L7-9, pipeline lives only in build-aux/ci/run-tests-inner.sh. Isolated by design (CI env exports), not a doc gap.

## Outcome

- Signal: useful

## Source Nodes

- GDK_DEBUG