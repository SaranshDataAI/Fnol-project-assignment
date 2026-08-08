# Autonomous Insurance Claims Processing Agent

## Optional Groq LLM extraction

This variant adds an optional LLM extraction layer for messy, free-form FNOL text. The LLM does **not** route claims: Python validation and deterministic routing rules remain the final authority.

1. Copy `.env.example` to `.env`.
2. Insert your Groq API key into `.env`. It is ignored by Git.
3. Install dependencies and use LLM extraction when needed:

```powershell
python main.py path\to\unstructured_fnol.txt --use-llm
```

Filled AcroForm PDFs still use the precise local form-field extractor even with `--use-llm`, avoiding an unnecessary API call. For a scanned or free-form document without usable form fields, LLM mode sends the extracted text to Groq and requests only the existing extraction schema. If routing-required information remains blank, the result is `Manual Review`; the model never makes the route decision.

Structured JSON inputs also stay local in LLM mode. For the API, add `?use_llm=true` to `POST /claims/process` when submitting unstructured TXT or text-based PDF input.

A lightweight, deterministic First Notice of Loss (FNOL) processor built for the Synapx assessment. It extracts the requested data, lists missing mandatory fields, recommends a workflow, and explains that recommendation.

## Features

- Accepts JSON, labelled TXT, and selectable-text PDF FNOL documents.
- Reads populated fields in AcroForm PDFs (including the supplied ACORD-style form), not only their visible page text.
- Produces the exact requested response structure: `extractedFields`, `missingFields`, `recommendedRoute`, and `reasoning`.
- Uses local deterministic rules; no customer claim data is sent to an AI service or stored by the API.
- Supports multiple other vehicles and non-vehicle third parties such as pedestrians, animals, and stolen property.
- Provides both a CLI and optional FastAPI endpoint.

## Routing policy

The assessment has overlapping rules, so this project makes its safety-first precedence explicit:

1. Missing routing-required FNOL field → `Manual Review`
2. Description contains `fraud`, `inconsistent`, or `staged` → `Investigation Flag`
3. Injury claim → `Specialist Queue`
4. Estimated damage below 25,000 → `Fast-track`
5. Otherwise → `Standard Review`

`claimType` is inferred from supplied facts when it is not explicitly present (for example, a populated `injured` list becomes `injury`). The extraction output retains every field requested in the brief, but routing only requires the fields actually captured by the ACORD-style FNOL form: policy, claimant/contact, loss, asset type, estimate, and claim type. `effectiveDates`, `attachments`, `assetId`, and duplicate `initialEstimate` do not block a route when they are unavailable in the form.

The brief uses a 25,000 threshold but its supplied examples do not declare a currency. The implementation compares the numeric estimate to `25000` and reports the assumption here.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run from the command line

```powershell
python main.py path\to\fnol.json
python main.py path\to\fnol.txt --output result.json
python main.py path\to\fnol.pdf
```

Example response:

```json
{
  "extractedFields": {},
  "missingFields": [],
  "recommendedRoute": "Fast-track",
  "reasoning": "Estimated damage is below the 25,000 threshold."
}
```

## Run tests

```powershell
python -m unittest discover -s tests -v
```

The tests cover the supplied scenario shapes—multi-vehicle, injury, hit-and-run-style unknown party, animal/non-vehicle party, commercial-style claim data, and theft/vandalism—as well as every routing rule.

## Demonstrate all routing branches

The original six inputs intentionally lack the brief's `effectiveDates` and `attachments` fields. Those values remain `null` in `extractedFields`, but no longer block routing because they are not supplied by the FNOL form.

For a strong technical-round demonstration, `data/demo_cases/` contains enriched versions of all six scenarios, plus a fraud-indicator case and a high-value property-damage case. They are complete JSON FNOLs solely for testing the routing logic.

```powershell
python run_demo.py
```

Expected routes:

| Scenario | Route |
| --- | --- |
| Rear-end injury; multi-vehicle injury; animal-strike injury; commercial pedestrian injury | Specialist Queue |
| Hit-and-run; vandalism/theft | Fast-track |
| Fraud-indicator wording | Investigation Flag |
| Complete 32,000 property-damage claim | Standard Review |

## Optional API

```powershell
uvicorn api:app --reload
```

Then upload a document at `POST /claims/process`, or use the interactive docs at `http://127.0.0.1:8000/docs`.

## Limitations and production next steps

This is intentionally a focused assessment implementation. A production service would add OCR for scanned PDFs, schema validation and confidence scoring, policy-system integration, authentication, audit logging with suitable PII controls, and human feedback loops.
