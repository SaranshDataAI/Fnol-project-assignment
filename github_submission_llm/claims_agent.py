"""Deterministic FNOL extraction, validation, and claims routing.

The module deliberately keeps business rules separate from input handling so that
the CLI and HTTP API return exactly the same assessment output.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

FRAUD_KEYWORDS = ("fraud", "inconsistent", "staged")
ROUTE_MANUAL_REVIEW = "Manual Review"
ROUTE_INVESTIGATION = "Investigation Flag"
ROUTE_SPECIALIST = "Specialist Queue"
ROUTE_FAST_TRACK = "Fast-track"
ROUTE_STANDARD_REVIEW = "Standard Review"

# The output retains every field named in the brief.  Only this smaller set is
# required to safely classify and route the ACORD FNOL form.  The form has no
# policy-effective-date or attachment fields, and its VIN/estimate fields can
# be legitimately unavailable at first notice of loss.
ROUTING_REQUIRED_FIELDS = (
    "policyNumber",
    "policyholderName",
    "date",
    "time",
    "location",
    "description",
    "claimant",
    "contactDetails",
    "assetType",
    "estimatedDamage",
    "claimType",
)


def read_document(path: str | Path) -> str:
    """Return the contents of a TXT, JSON, or selectable-text PDF document."""
    document = Path(path)
    if not document.is_file():
        raise FileNotFoundError(f"Input document does not exist: {document}")
    return _read_bytes(document.name, document.read_bytes())


def _read_bytes(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".json"}:
        return content.decode("utf-8-sig")
    if suffix == ".pdf":
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise RuntimeError("PDF support requires: pip install -r requirements.txt") from exc
        with pdfplumber.open(BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    raise ValueError("Supported input formats are .json, .txt, and .pdf.")


def read_upload(filename: str, content: bytes) -> str:
    """Read an upload in memory; no customer document is written to disk."""
    return _read_bytes(filename, content)


def _pdf_form_data(content: bytes) -> dict[str, Any] | None:
    """Convert populated AcroForm fields into the same shape as JSON FNOL input.

    A form PDF stores completed values separately from the page's visible text;
    extracting its text alone therefore returns just labels and legal notices.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("PDF form support requires: pip install -r requirements.txt") from exc

    fields = PdfReader(BytesIO(content)).get_fields() or {}

    def value(*names: str) -> Any:
        for name in names:
            field = fields.get(name)
            if field and _clean(field.get("/V")) not in (None, "/Off"):
                return _clean(field.get("/V"))
        return None

    # The ACORD template has descriptive names for most fields.  A small number
    # are anonymous (Text3/Text4/Text7/Text45), so those stable field IDs are
    # mapped as well after inspecting the template.
    insured_name = value("NAME OF INSURED First Middle Last")
    contact_name = value("NAME OF CONTACT First Middle Last")
    injury_name = value("NAME  ADDRESSRow1")
    injury_extent = value("EXTENT OF INJURY")
    populated = any(
        (insured_name, contact_name, value("Text7"), value("DESCRIPTION OF ACCIDENT ACORD 101 Additional Remarks Schedule may be attached if more space is required"))
    )
    if not populated:
        return None

    return {
        "policy_number": value("Text7"),
        "date_of_loss": value("Text3"),
        "time_of_loss": value("Text4"),
        "insured": {
            "name": insured_name,
            "primary_phone": value("PHONE  CELL HOME BUS PRIMARY"),
        },
        "contact": {
            "name": contact_name,
            "primary_phone": value("PHONE  CELL HOME BUS PRIMARY_2"),
        },
        "loss": {
            "location_street": value("STREET LOCATION OF LOSS"),
            "location_city_state_zip": value("CITY STATE ZIP"),
            "description": value("DESCRIPTION OF ACCIDENT ACORD 101 Additional Remarks Schedule may be attached if more space is required"),
        },
        "insured_vehicle": {
            "body": value("TYPE BODY"),
            "vin": value("VIN"),
            "estimate_amount": value("Text45", "ESTIMATE AMOUNT_2"),
        },
        "other_vehicle": {
            "non_vehicle": bool(value("Check Box46")),
            "property_description": value("DESCRIBE PROPERTY Other Than Vehicle"),
        },
        "injured": ([{"name": injury_name, "extent": injury_extent}] if injury_name or injury_extent else []),
    }


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = re.sub(r"\s+", " ", value).strip()
        return value or None
    return value


def _get(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = _clean(mapping.get(key))
        if value is not None:
            return value
    return None


def _amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:[$₹]|USD|INR)?\s*([\d,]+(?:\.\d{1,2})?)", value, re.I)
    return float(match.group(1).replace(",", "")) if match else None


def _infer_claim_type(description: str | None, injuries: Any) -> str | None:
    text = (description or "").lower()
    has_negative_injury_statement = bool(re.search(r"\b(no|without|denies?)\s+injur\w*\b", text))
    if injuries or (
        not has_negative_injury_statement
        and re.search(r"\b(injur\w*|ambulance|hospital|medical)\b", text)
    ):
        return "injury"
    if re.search(r"hit[ -]?and[ -]?run|fled scene", text):
        return "hit_and_run"
    if re.search(r"theft|stolen|vandal", text):
        return "theft_vandalism"
    if re.search(r"animal|elk|deer|wildlife", text):
        return "animal_strike"
    return "property_damage" if description else None


def _other_parties(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        value
        for key, value in data.items()
        if key.startswith("other_vehicle") and isinstance(value, dict)
    ]


def _from_json(data: dict[str, Any]) -> dict[str, Any]:
    insured = data.get("insured") if isinstance(data.get("insured"), dict) else {}
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
    loss = data.get("loss") if isinstance(data.get("loss"), dict) else {}
    vehicle = data.get("insured_vehicle") if isinstance(data.get("insured_vehicle"), dict) else {}
    parties = _other_parties(data)
    description = _get(loss, "description") or _get(data, "description")
    estimated_damage = _amount(_get(vehicle, "estimate_amount", "estimated_damage"))
    claimant = _get(contact, "name") or _get(insured, "name")
    third_parties = [
        _get(party, "driver_name", "owner_name", "property_description") for party in parties
    ]
    return {
        "policyInformation": {
            "policyNumber": _get(data, "policy_number"),
            "policyholderName": _get(insured, "name") or _get(data, "policyholder_name"),
            "effectiveDates": _get(data, "effective_dates", "policy_effective_dates"),
        },
        "incidentInformation": {
            "date": _get(data, "date_of_loss", "incident_date"),
            "time": _get(data, "time_of_loss", "incident_time"),
            "location": ", ".join(
                item for item in (_get(loss, "location_street"), _get(loss, "location_city_state_zip")) if item
            ) or _get(data, "location"),
            "description": description,
        },
        "involvedParties": {
            "claimant": claimant,
            "thirdParties": [party for party in third_parties if party] or None,
            "contactDetails": _get(contact, "primary_phone", "phone", "email")
            or _get(insured, "primary_phone", "primary_email"),
        },
        "assetDetails": {
            "assetType": _get(vehicle, "body", "asset_type"),
            "assetId": _get(vehicle, "vin", "asset_id"),
            "estimatedDamage": estimated_damage,
        },
        "claimType": _get(data, "claim_type") or _infer_claim_type(description, data.get("injured")),
        "attachments": _get(data, "attachments"),
        "initialEstimate": _amount(_get(data, "initial_estimate")) or estimated_damage,
    }


def _first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _label(text: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(rf"(?im)^\s*{re.escape(label)}\s*:?\s*(.+?)\s*$", text)
        if match:
            value = _clean(match.group(1))
            if _is_template_text(value):
                continue
            return value
    return None


def _is_template_text(value: Any) -> bool:
    """Reject headings/placeholders extracted from blank, table-based PDF forms."""
    if not isinstance(value, str):
        return False
    uppercase_value = value.upper()
    headings = {
        "CONTACT",
        "LINE OF BUSINESS",
        "POLICE OR FIRE DEPARTMENT CONTACTED",
        "INSURED'S MAILING ADDRESS",
        "CONTACT'S MAILING ADDRESS",
    }
    return (
        uppercase_value in headings
        or "(FIRST, MIDDLE, LAST)" in uppercase_value
        or "ADDITIONAL REMARKS SCHEDULE" in uppercase_value
        or "POLICE OR FIRE DEPARTMENT CONTACTED" in uppercase_value
        or uppercase_value.endswith("ADDRESS:")
    )


def _from_text(text: str) -> dict[str, Any]:
    description = _label(text, "Description of Accident", "Description")
    estimated_damage = _amount(_label(text, "Estimated Damage", "Estimate Amount", "Initial Estimate"))
    return {
        "policyInformation": {
            "policyNumber": _label(text, "Policy Number"),
            "policyholderName": _label(text, "Policyholder Name", "Name of Insured"),
            "effectiveDates": _label(text, "Effective Dates", "Policy Effective Dates"),
        },
        "incidentInformation": {
            "date": _label(text, "Date of Loss", "Incident Date"),
            "time": _label(text, "Time of Loss", "Incident Time"),
            "location": _label(text, "Location of Loss", "Location"),
            "description": description,
        },
        "involvedParties": {
            "claimant": _label(text, "Claimant", "Name of Contact", "Name of Insured"),
            "thirdParties": _label(text, "Third Parties", "Third Party"),
            "contactDetails": _label(text, "Contact Details", "Phone", "Email", "E-mail"),
        },
        "assetDetails": {
            "assetType": _label(text, "Asset Type", "Body Type"),
            "assetId": _label(text, "Asset ID", "VIN", "V.I.N."),
            "estimatedDamage": estimated_damage,
        },
        "claimType": _label(text, "Claim Type") or _infer_claim_type(description, None),
        "attachments": _label(text, "Attachments"),
        "initialEstimate": _amount(_label(text, "Initial Estimate")) or estimated_damage,
    }


def extract_fields(text: str) -> dict[str, Any]:
    """Extract the required field structure from JSON or labelled text."""
    parsed = _first_json_object(text)
    return _from_json(parsed) if parsed else _from_text(text)


def _flatten(extracted: dict[str, Any]) -> dict[str, Any]:
    return {
        **extracted["policyInformation"],
        **extracted["incidentInformation"],
        **extracted["involvedParties"],
        **extracted["assetDetails"],
        "claimType": extracted["claimType"],
        "attachments": extracted["attachments"],
        "initialEstimate": extracted["initialEstimate"],
    }


def route_claim(extracted: dict[str, Any]) -> tuple[list[str], str, str]:
    """Return missing fields and a route using documented safety-first precedence."""
    fields = _flatten(extracted)
    missing = [name for name in ROUTING_REQUIRED_FIELDS if fields.get(name) in (None, "", [])]
    if missing:
        return missing, ROUTE_MANUAL_REVIEW, f"Mandatory fields missing: {', '.join(missing)}."
    description = str(fields["description"]).lower()
    if any(keyword in description for keyword in FRAUD_KEYWORDS):
        return [], ROUTE_INVESTIGATION, "Potential fraud indicator found in the incident description."
    if str(fields["claimType"]).lower() == "injury":
        return [], ROUTE_SPECIALIST, "Claim type is injury and requires specialist handling."
    if fields["estimatedDamage"] < 25_000:
        return [], ROUTE_FAST_TRACK, "Estimated damage is below the 25,000 threshold."
    return [], ROUTE_STANDARD_REVIEW, "No exception rule applies."


def process_text(text: str) -> dict[str, Any]:
    extracted = extract_fields(text)
    missing, route, reasoning = route_claim(extracted)
    return {
        "extractedFields": extracted,
        "missingFields": missing,
        "recommendedRoute": route,
        "reasoning": reasoning,
    }


def process_text_with_llm(text: str) -> dict[str, Any]:
    """Use Groq only for extraction, then apply the same deterministic rules."""
    from llm_extractor import extract_with_groq

    extracted = extract_with_groq(text)
    missing, route, reasoning = route_claim(extracted)
    return {
        "extractedFields": extracted,
        "missingFields": missing,
        "recommendedRoute": route,
        "reasoning": reasoning,
    }


def process_document(path: str | Path) -> dict[str, Any]:
    document = Path(path)
    if document.suffix.lower() == ".pdf":
        return _process_pdf(document.name, document.read_bytes())
    return process_text(read_document(document))


def process_document_with_llm(path: str | Path) -> dict[str, Any]:
    """Use Groq for unstructured text; retain precise AcroForm extraction."""
    document = Path(path)
    if document.suffix.lower() == ".json":
        return process_document(document)
    if document.suffix.lower() == ".pdf":
        form_data = _pdf_form_data(document.read_bytes())
        if form_data is not None:
            return process_text(json.dumps(form_data))
    return process_text_with_llm(read_document(document))


def process_upload(filename: str, content: bytes) -> dict[str, Any]:
    if Path(filename).suffix.lower() == ".pdf":
        return _process_pdf(filename, content)
    return process_text(read_upload(filename, content))


def process_upload_with_llm(filename: str, content: bytes) -> dict[str, Any]:
    """LLM-mode equivalent of process_upload, preserving local structured paths."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        return process_upload(filename, content)
    if suffix == ".pdf":
        form_data = _pdf_form_data(content)
        if form_data is not None:
            return process_text(json.dumps(form_data))
    return process_text_with_llm(read_upload(filename, content))


def _process_pdf(filename: str, content: bytes) -> dict[str, Any]:
    form_data = _pdf_form_data(content)
    if form_data is not None:
        return process_text(json.dumps(form_data))
    return process_text(read_upload(filename, content))
