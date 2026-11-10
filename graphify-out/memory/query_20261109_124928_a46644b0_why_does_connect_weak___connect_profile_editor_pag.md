---
type: "query"
date: "2026-11-09T12:49:28.716474+00:00"
question: "Why does connect_weak() connect Profile Editor Pages to many communities?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["connect_weak()"]
---

# Q: Why does connect_weak() connect Profile Editor Pages to many communities?

## Answer

Expanded from original query via vocab: [connect weak profile editor pages timeline]. Then traversed BFS+explain: connect_weak src/pages.py L49 degree 217, imported by 40+ files (admin 16, compose 10, profileedit 10...). It is the shared weak-signal helper from pages.py (avoids C ref cycles) so cross-community degree is structural fact, not a bug.

## Outcome

- Signal: useful

## Source Nodes

- connect_weak()