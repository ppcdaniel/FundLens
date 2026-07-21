# Evaluation protocol

The repository includes ten fictional, redistributable factsheets and labeled expected extractions. They intentionally vary dates, currencies, income treatment, fee terminology, and missing disclosures.

## Metrics

- Field extraction accuracy: exact or normalized match per disclosed field.
- Citation-page accuracy: expected page equals cited page.
- Evidence-support accuracy: cited text actually entails the extracted value.
- Missing-field detection accuracy: `not_disclosed` where the label is absent.
- Unsupported-claim rate: unsupported claims divided by all generated claims.
- Processing time: wall-clock seconds per document and per brief.
- Failure rate: failed documents or briefs divided by attempts.

## Procedure

1. Pin the code, prompt version, model identifier, and evaluation dataset commit.
2. Run each factsheet in a fresh session with deterministic generation settings.
3. Compare output to `sample_data/expected_extractions/*.json`.
4. Independently review evidence entailment and checker labels.
5. Report counts, denominators, uncertainty, latency environment, and failures.

No accuracy claim should be published until this protocol has been run. The included labels are an evaluation surface, not a performance claim.

