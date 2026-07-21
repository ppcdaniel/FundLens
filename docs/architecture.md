# Architecture

FundLens is a typed modular monolith. `app.py` coordinates Streamlit state and delegates all business decisions.

```text
Browser / Streamlit session
  ├── upload + evidence-review UI
  ├── PyMuPDF DocumentParser
  ├── FundExtractor → injected Gemma client
  ├── ComparisonService
  ├── FinancialAnalyticsService (pure calculations)
  ├── BriefGenerator → injected Gemma client
  ├── EvidenceChecker
  └── ExportService
```

## Boundaries

- `models/` contains strict Pydantic contracts and enums.
- `services/` contains application logic. AI and document parsing depend on protocols so tests can inject fakes.
- `ui/` renders and edits state but does not parse documents, calculate metrics, or make model calls.
- `prompts/` contains versioned prompt assets.

## Data lifecycle

PDF bytes are validated before parsing. Each parse uses a uniquely isolated temporary directory that is removed on scope exit, computes a SHA-256 processing identity, and extracts bounded page text and coordinates. Parsed data and original upload bytes remain only in the current Streamlit session until the upload changes, **Forget API key** is selected, or the session expires. The API key and constructed AI client are never placed in `st.cache_*`.

## Evidence precedence

For downstream comparison and generation: `corrected` → `approved` → other extracted values. Rejected and unresolved facts are not represented as established facts. Every supported generated claim cites an evidence identifier or deterministic metric identifier.

## Failure behavior

Malformed model output fails Pydantic validation. The extractor permits one schema-constrained repair call and then returns an explicit extraction failure. Missing fields are represented as `not_disclosed`; scanned PDFs are reported as unsupported rather than silently producing empty evidence.
