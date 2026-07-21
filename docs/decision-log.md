# Decision log

## 2026-07-21 — Modular monolith

Chosen to keep the MVP deployable on Community Cloud while retaining typed seams for parsing, AI, analytics, and export. A database, queue, cache service, and authentication would add operational and privacy cost without serving the 14-day workflow.

## 2026-07-21 — Synthetic factsheet corpus

Ten fictional PDFs provide a legal, reproducible evaluation and demo surface. They deliberately exercise comparison warnings and missing fields; they are not representations of real products.

## 2026-07-21 — Session-memory BYOK

The product does not accept a server-owned key. Per-session memory and an explicit forget action meet the privacy model while preserving a hosted demo. Docker is documented for users who require local processing.

## 2026-07-21 — Evidence-first output

Reviewer-approved facts and deterministic metrics form an evidence registry. Generated prose is checked against that registry, and unsupported claims are withheld from normal exports.

## 2026-07-21 — Editorial research visual system

The interface uses high whitespace, restrained cards, strong typographic hierarchy, deep-ink text, emerald state accents, and soft-lilac supporting surfaces. This keeps dense financial evidence legible without imitating a trading terminal.

## 2026-07-21 — Compact provider extraction contract

The first extraction prompt repeated the full domain schema and trusted metadata for every field, causing Gemma 4 31B requests to exceed the bounded request deadline. Extraction prompt v2 keeps all 21 typed field values and evidence attributes but omits provider-controlled source identity and review state. FundLens injects the parser-derived document hash and pending review state locally, then validates the unchanged full domain model and exact page citations. This reduces provider work without weakening evidence traceability or the single-repair rule.
