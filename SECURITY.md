# Security policy

## Supported versions

Security fixes target the latest `main` branch during the MVP phase.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature. Do not include live API keys, client documents, or personal information in a report.

## Security model

FundLens is BYOK. A Google AI Studio key exists only in the user's Streamlit session memory and is sent from the browser to the Streamlit server. It is never written to disk, cached globally, included in reports, or logged. Selecting **Forget API key** removes the key and all AI-derived session state.

Uploaded documents are validated, copied into a uniquely isolated processing directory, and deleted when parsing exits, including on failure. Their original upload bytes remain scoped to the active Streamlit session until the upload changes, the user selects **Forget API key**, or the session expires. Deployments should use HTTPS and should not enable server request logging that records request bodies.
