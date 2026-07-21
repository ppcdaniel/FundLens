# Responsible AI

## Intended use

FundLens assists a human researcher in organizing issuer disclosures and historical calculations. It does not determine suitability or recommend a transaction.

## Controls

- Structured extraction uses strict schemas, bounded inputs, source-page validation, and a single repair attempt.
- Missing facts stay missing; the model is instructed not to infer them.
- Human review status is explicit for every field.
- Financial calculations are pure Python and carry methodology metadata.
- Brief language is claim-split and evidence-classified.
- Unsupported/conflicting claims are highlighted and excluded from export by default.
- Interpretations are labeled and traceable to underlying facts.

## Known limitations

Issuer documents can be ambiguous, stale, inconsistent, or optimized for marketing. PDF text extraction may scramble complex layouts. The initial release does not OCR scanned pages. Historical returns are sensitive to the chosen share class, adjusted-price definition, currency, date alignment, and observation window. Evidence checking reduces risk but cannot prove completeness or truth.

## Human responsibility

Review source pages, reconcile share-class and period differences, confirm material terms with current legal documents, investigate missing disclosures, and apply the organization's compliance and suitability process before communicating or acting.

