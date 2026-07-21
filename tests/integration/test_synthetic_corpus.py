"""Integration checks for the redistributable factsheet evaluation corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fundlens.models.fund import FundFactsheet
from fundlens.services.pdf_parser import PyMuPDFDocumentParser

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = REPOSITORY_ROOT / "sample_data"


def _complete_evidence_payload(expected_payload: dict[str, Any]) -> dict[str, Any]:
    """Add invariant evidence metadata to concise human-authored labels."""

    source_document = "a" * 64
    completed_fields: dict[str, Any] = {}
    for field_name, field in expected_payload["fields"].items():
        disclosed = field["status"] == "disclosed"
        completed_fields[field_name] = {
            **field,
            "source_document": source_document,
            "supporting_text": f"Labeled source passage for {field_name}" if disclosed else None,
            "confidence": 1.0 if disclosed else 0.0,
            "review_status": "pending",
        }
    return completed_fields


def test_all_ten_synthetic_factsheets_are_text_native_and_schema_labeled() -> None:
    """Every generated PDF should parse and every expected extraction should validate."""

    manifest = json.loads((SAMPLE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    parser = PyMuPDFDocumentParser(maximum_page_count=3)

    assert manifest["synthetic"] is True
    assert len(manifest["funds"]) == 10

    for item in manifest["funds"]:
        factsheet_path = SAMPLE_ROOT / "synthetic_factsheets" / item["factsheet"]
        parsed_document = parser.parse(
            factsheet_path.read_bytes(),
            file_name=factsheet_path.name,
            mime_type="application/pdf",
        )
        expected_path = SAMPLE_ROOT / "expected_extractions" / item["expected_extraction"]
        expected_payload = json.loads(expected_path.read_text(encoding="utf-8"))

        assert parsed_document.page_count == 3
        assert all(page.text.strip() for page in parsed_document.pages)
        FundFactsheet.model_validate_json(
            json.dumps(_complete_evidence_payload(expected_payload), ensure_ascii=False)
        )
