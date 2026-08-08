"""Optional Groq-powered extraction for unstructured FNOL text.

This module never makes a routing decision. It only turns difficult free-form
text into the application's extraction schema.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """You extract First Notice of Loss (FNOL) documents.
Return only valid JSON in this shape:
{"policyInformation":{"policyNumber":null,"policyholderName":null,"effectiveDates":null},"incidentInformation":{"date":null,"time":null,"location":null,"description":null},"involvedParties":{"claimant":null,"thirdParties":null,"contactDetails":null},"assetDetails":{"assetType":null,"assetId":null,"estimatedDamage":null},"claimType":null,"attachments":null,"initialEstimate":null}
Use null for anything not stated. Do not invent facts. Claim type may only be
injury, property_damage, hit_and_run, theft_vandalism, or animal_strike when
the document supports it."""


def _load_dotenv() -> None:
    """Load a local .env without adding a runtime dependency."""
    env_file = Path(__file__).with_name(".env")
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _json_content(content: str) -> dict[str, Any]:
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("LLM response was not a JSON object.")
    return value


def normalise_llm_extraction(raw: dict[str, Any]) -> dict[str, Any]:
    """Guarantee the project's existing result schema before rule validation."""
    def section(name: str) -> dict[str, Any]:
        value = raw.get(name)
        return value if isinstance(value, dict) else {}

    policy, incident = section("policyInformation"), section("incidentInformation")
    parties, asset = section("involvedParties"), section("assetDetails")
    third_parties = parties.get("thirdParties")
    if isinstance(third_parties, str):
        third_parties = [third_parties]
    if not isinstance(third_parties, list):
        third_parties = None
    return {
        "policyInformation": {"policyNumber": policy.get("policyNumber"), "policyholderName": policy.get("policyholderName"), "effectiveDates": policy.get("effectiveDates")},
        "incidentInformation": {"date": incident.get("date"), "time": incident.get("time"), "location": incident.get("location"), "description": incident.get("description")},
        "involvedParties": {"claimant": parties.get("claimant"), "thirdParties": third_parties, "contactDetails": parties.get("contactDetails")},
        "assetDetails": {"assetType": asset.get("assetType"), "assetId": asset.get("assetId"), "estimatedDamage": asset.get("estimatedDamage")},
        "claimType": raw.get("claimType"),
        "attachments": raw.get("attachments"),
        "initialEstimate": raw.get("initialEstimate"),
    }


def extract_with_groq(document_text: str) -> dict[str, Any]:
    """Ask Groq for extraction only; Python handles all final decisions."""
    _load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key_here":
        raise RuntimeError("Set GROQ_API_KEY in .env (copy .env.example first) or in your environment.")
    try:
        from groq import Groq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("LLM mode requires: pip install -r requirements.txt") from exc
    completion = Groq(api_key=api_key).chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract this FNOL document:\n\n{document_text}"},
        ],
    )
    content = completion.choices[0].message.content
    if not content:
        raise RuntimeError("Groq returned an empty extraction response.")
    return normalise_llm_extraction(_json_content(content))
