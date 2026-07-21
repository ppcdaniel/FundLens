# Product brief

## Problem

Fund factsheets are compact but inconsistent. Labels, reporting periods, fee definitions, currencies, share classes, and evidence locations vary by issuer. Analysts need a fast comparison without losing the distinction between disclosed facts, calculations, and interpretation.

## User outcome

A user supplies two or three factsheets, reviews page-cited normalized fields, optionally supplies adjusted-price histories, records client requirements, and exports a one-page evidence-checked comparison brief.

## Product principles

- Never infer an undisclosed fact.
- A reviewer-approved or reviewer-corrected value wins downstream.
- Calculations are deterministic and never delegated to a language model.
- Unsupported claims are visible and excluded from export by default.
- Key and document handling are session-scoped and ephemeral.
- Comparisons explain limitations instead of manufacturing equivalence.

## In scope

Factsheet PDF extraction, evidence review, normalized comparison, client-requirement capture, adjusted-price analytics, three brief tones, claim checking, Markdown/PDF export, synthetic evaluation data, Docker, CI, and Community Cloud deployment.

## Out of scope

Accounts, authentication, persistent storage, live prices, broker connections, portfolio optimization, forecasts, recommendations, collaboration, general chat, and full prospectus analysis.

## Definition of done

A new user can complete the entire workflow with two included synthetic factsheets and price files, understand each evidence and calculation source, export only reviewed material, and clear the key/session state.

