# Privacy

FundLens has no database, accounts, application-owned analytics tracker, or persistent document store. A hosted platform may collect operational telemetry under its own terms.

- Uploaded PDFs and CSV files are held only in the active Streamlit session memory. PDF parsing uses a uniquely isolated temporary copy that is deleted immediately when parsing completes or fails.
- A user-provided Google AI Studio key is held in per-session server memory. Streamlit necessarily transmits the entered value from the browser to the Python server.
- Extracted document text is sent to Google's Gemini API only when the user initiates extraction or brief analysis.
- The hosted operator can technically access server memory and process logs. Users who do not trust a hosted operator should run FundLens locally with Docker.
- Exported reports never contain API keys and omit unsupported claims unless explicitly approved.

Closing the browser session eventually releases server-side session memory. Use **Forget API key** to clear sensitive session values immediately.
