import json
import unittest
from pathlib import Path

from claims_agent import (
    ROUTE_FAST_TRACK,
    ROUTE_INVESTIGATION,
    ROUTE_MANUAL_REVIEW,
    ROUTE_SPECIALIST,
    extract_fields,
    process_text,
    route_claim,
)


def complete_claim(**overrides: object) -> dict:
    claim = {
        "date_of_loss": "08/02/2026",
        "time_of_loss": "17:45",
        "policy_number": "POL-123",
        "effective_dates": "01/01/2026 - 01/01/2027",
        "attachments": ["photos.jpg"],
        "insured": {"name": "Alex Morgan", "primary_phone": "555-0100"},
        "contact": {"name": "Alex Morgan", "primary_phone": "555-0100"},
        "loss": {
            "location_street": "100 Main Street",
            "location_city_state_zip": "Austin, TX 78701",
            "description": "Minor collision with a parked vehicle.",
        },
        "insured_vehicle": {
            "body": "Sedan",
            "vin": "VIN123",
            "estimate_amount": 12_000,
        },
        "other_vehicle": {"driver_name": "Sam Lee"},
        "injured": [],
    }
    claim.update(overrides)
    return claim


class ClaimsAgentTests(unittest.TestCase):
    def route(self, claim: dict) -> tuple[list[str], str, str]:
        return route_claim(extract_fields(json.dumps(claim)))

    def test_complete_low_value_claim_is_fast_tracked(self) -> None:
        missing, route, _ = self.route(complete_claim())
        self.assertEqual(missing, [])
        self.assertEqual(route, ROUTE_FAST_TRACK)

    def test_missing_assessment_fields_take_priority(self) -> None:
        claim = complete_claim()
        del claim["insured_vehicle"]["estimate_amount"]
        missing, route, _ = self.route(claim)
        self.assertEqual(route, ROUTE_MANUAL_REVIEW)
        self.assertEqual(missing, ["estimatedDamage"])

    def test_fraud_keyword_routes_to_investigation(self) -> None:
        claim = complete_claim()
        claim["loss"]["description"] = "Potential staged collision with inconsistent statements."
        _, route, _ = self.route(claim)
        self.assertEqual(route, ROUTE_INVESTIGATION)

    def test_injury_routes_to_specialist(self) -> None:
        claim = complete_claim(injured=[{"name": "Alex Morgan", "extent": "Whiplash"}])
        extracted = extract_fields(json.dumps(claim))
        self.assertEqual(extracted["claimType"], "injury")
        _, route, _ = route_claim(extracted)
        self.assertEqual(route, ROUTE_SPECIALIST)

    def test_multi_vehicle_input_collects_all_third_parties(self) -> None:
        claim = complete_claim(
            other_vehicle_1={"driver_name": "Carlos Martinez"},
            other_vehicle_2={"driver_name": "Amanda Wilson"},
        )
        del claim["other_vehicle"]
        extracted = extract_fields(json.dumps(claim))
        self.assertEqual(extracted["involvedParties"]["thirdParties"], ["Carlos Martinez", "Amanda Wilson"])

    def test_non_vehicle_third_party_is_extracted(self) -> None:
        claim = complete_claim(other_vehicle={"non_vehicle": True, "property_description": "Wildlife - Elk"})
        extracted = extract_fields(json.dumps(claim))
        self.assertEqual(extracted["involvedParties"]["thirdParties"], ["Wildlife - Elk"])

    def test_output_matches_assessment_contract(self) -> None:
        result = process_text(json.dumps(complete_claim()))
        self.assertEqual(set(result), {"extractedFields", "missingFields", "recommendedRoute", "reasoning"})

    def test_blank_pdf_template_headings_are_not_extracted_as_values(self) -> None:
        text = """Policy Number: CONTACT
Name of Insured: (First, Middle, Last) INSURED'S MAILING ADDRESS
Location: POLICE OR FIRE DEPARTMENT CONTACTED
Description: (ACORD 101, Additional Remarks Schedule, may be attached if more space is required)
Contact Details: LINE OF BUSINESS
"""
        extracted = extract_fields(text)
        self.assertIsNone(extracted["policyInformation"]["policyNumber"])
        self.assertIsNone(extracted["policyInformation"]["policyholderName"])
        self.assertIsNone(extracted["incidentInformation"]["location"])
        self.assertIsNone(extracted["incidentInformation"]["description"])

    def test_complete_demo_cases_show_every_route(self) -> None:
        expected_routes = {
            "01_rear_end_injury.json": ROUTE_SPECIALIST,
            "02_multi_vehicle_injury.json": ROUTE_SPECIALIST,
            "03_hit_and_run.json": ROUTE_FAST_TRACK,
            "04_animal_strike_injury.json": ROUTE_SPECIALIST,
            "05_commercial_pedestrian_injury.json": ROUTE_SPECIALIST,
            "06_vandalism_theft.json": ROUTE_FAST_TRACK,
            "07_fraud_indicator.json": ROUTE_INVESTIGATION,
            "08_high_value_property_damage.json": "Standard Review",
        }
        demo_directory = Path(__file__).parent.parent / "data" / "demo_cases"
        for name, expected_route in expected_routes.items():
            result = process_text((demo_directory / name).read_text(encoding="utf-8"))
            self.assertEqual(result["missingFields"], [], name)
            self.assertEqual(result["recommendedRoute"], expected_route, name)

    def test_acroform_pdf_values_are_extracted(self) -> None:
        from claims_agent import process_document

        pdf = Path(__file__).parent.parent / "data" / "PDF" / "05_commercial_pedestrian_injury_filled.pdf"
        if not pdf.exists():
            self.skipTest("Optional local filled-PDF fixture is not available")
        result = process_document(pdf)
        extracted = result["extractedFields"]
        self.assertEqual(extracted["policyInformation"]["policyNumber"], "NW-CA-8829104")
        self.assertEqual(extracted["policyInformation"]["policyholderName"], "QuickBite Catering LLC")
        self.assertEqual(extracted["incidentInformation"]["date"], "08/01/2026")
        self.assertEqual(extracted["assetDetails"]["estimatedDamage"], 1850.0)
        self.assertEqual(extracted["claimType"], "injury")
        self.assertEqual(result["missingFields"], [])
        self.assertEqual(result["recommendedRoute"], ROUTE_SPECIALIST)

    def test_llm_output_normalisation_preserves_the_contract(self) -> None:
        from llm_extractor import normalise_llm_extraction

        extracted = normalise_llm_extraction({
            "policyInformation": {"policyNumber": "P-1"},
            "involvedParties": {"thirdParties": "Sam"},
        })
        self.assertEqual(extracted["policyInformation"]["policyNumber"], "P-1")
        self.assertEqual(extracted["involvedParties"]["thirdParties"], ["Sam"])
        self.assertIn("estimatedDamage", extracted["assetDetails"])

    def test_llm_mode_keeps_structured_json_local(self) -> None:
        from claims_agent import process_document_with_llm

        document = Path(__file__).parent.parent / "data" / "demo_cases" / "03_hit_and_run.json"
        self.assertEqual(process_document_with_llm(document)["recommendedRoute"], ROUTE_FAST_TRACK)


if __name__ == "__main__":
    unittest.main()
