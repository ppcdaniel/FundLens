# FundLens

FundLens is a privacy-conscious, evidence-grounded workspace for comparing two or three ETF or fund factsheets. It extracts normalized facts with page citations, keeps a human reviewer in control, calculates historical metrics with deterministic Python, checks generated claims, and exports an approved research brief.

> FundLens provides research and decision support, not financial advice. It does not recommend a “best” fund or issue buy, sell, or suitability conclusions.

## What the MVP does

1. Accepts a Google AI Studio API key for the current browser session only.
2. Validates and parses up to three text-based factsheet PDFs.
3. Uses the fixed `gemma-4-31b-it` model to produce strict structured data.
4. Lets the user approve, correct, reject, or leave each cited field unresolved.
5. Compares issuer disclosures and explains comparability limitations.
6. Validates adjusted-price CSVs and calculates reproducible risk/return metrics.
7. Maps client requirements to observable trade-offs and due-diligence questions.
8. Checks each generated claim against document evidence or calculated metrics.
9. Exports reviewed Markdown or PDF without unapproved unsupported claims.

## Quick start with uv

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run streamlit run app.py
```

Open `http://localhost:8501`. Start with two PDFs from `sample_data/synthetic_factsheets/` and the matching CSVs from `sample_data/prices/`.

Generate or refresh the synthetic dataset with:

```bash
uv run python scripts/generate_sample_data.py
```

## Docker

```bash
docker compose up --build
```

The container uses a read-only root filesystem and an in-memory temporary directory. A small VPS should provide at least 2 GB of RAM.

## Bring-your-own-key privacy

The password field hides the key on screen, but Streamlit sends it to the Python server. FundLens holds it only in that user's session memory—never in a file, cookie, database, shared cache, report, or application log. **Forget API key** clears the key and related AI state.

Users who do not trust a hosted operator with in-memory access should run the Docker deployment locally. See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md).

## Price CSV format

```csv
date,adjusted_close
2025-01-02,100.00
2025-01-03,100.40
```

Prices must be positive numbers with unique, valid dates. FundLens reports the observation period and its methodology with every calculation. It never asks the language model to calculate financial metrics.

## Development checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src app.py
uv run pytest
```

Gemma calls are replaced by deterministic fakes in CI. No real API key is required or permitted in tests.

## Deployment

Push the public repository to GitHub, then create a Streamlit Community Cloud app using branch `main` and entrypoint `app.py`. Do not configure a server-owned AI key or add a `secrets.toml` file. Community Cloud reads `uv.lock` and defaults to Python 3.12.

## Documentation

- [Product scope](docs/product-brief.md)
- [Architecture](docs/architecture.md)
- [Evaluation protocol](docs/evaluation.md)
- [Responsible AI](docs/responsible-ai.md)
- [Decision log](docs/decision-log.md)

## License

MIT
