---
type: "query"
date: "2026-11-09T12:49:28.789654+00:00"
question: "Are the 14 inferred relationships involving BabbleWindow actually correct?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["BabbleWindow"]
---

# Q: Are the 14 inferred relationships involving BabbleWindow actually correct?

## Answer

Expanded from original query via vocab: [babble window]. Traversed BFS+explain: 14 uses INFERRED at window.py L451 MastodonClient, L918 TimelinePage, L803 AvatarCache, L205 SidebarHost, L274 DesktopNotifier, L1283 ApiTimelineSource, L1238 NotificationsPage, L275 StreamingManager. Grep confirms all 8+ imported and instantiated in window.py - correct as uses, INFERRED only because AST infers use vs implements.

## Outcome

- Signal: useful

## Source Nodes

- BabbleWindow