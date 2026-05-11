# Architecture diagrams

ASCII versions of all diagrams live in `PLAN.md` (single source of truth). This folder holds **rendered PNG/SVG** exports for inclusion in slides, RFCs, or external sharing.

## Planned exports

| Diagram                          | Source location                    | Export target       |
| -------------------------------- | ---------------------------------- | ------------------- |
| Track A four-plane control plane | PLAN.md §4.2                       | `track-a-control-plane.png` |
| Track A data flow                | PLAN.md §4.3                       | `track-a-data-flow.png`     |
| Track B medallion architecture   | PLAN.md §5.3                       | `track-b-medallion.png`     |
| Track A vs Track B migration     | (derived from COMPARISON.md §5)    | `migration-overview.png`    |

## Rendering tool

Use [Excalidraw](https://excalidraw.com) (open `.excalidraw` files committed alongside the PNG) or [draw.io](https://drawio.com). Both produce versioned diagram sources that survive being re-edited.

Avoid Mermaid for these (multi-pane diagrams render poorly in Mermaid; PNG quality is better for external sharing).
