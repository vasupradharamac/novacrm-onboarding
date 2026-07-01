"""Tests for the Intake Agent's email parsing and validation guardrail.

These mock the OpenAI client entirely, so they run in seconds, cost
nothing, and need zero live accounts — including the ones currently
blocked on signup. This directly covers Part 4's "Validation" requirement:
the Intake Agent correctly rejects incomplete or malformed emails.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from email_parser import MissingFieldsError, ParsedDeal, parse_deal_email


def _mock_openai_response(extracted_dict: dict):
    """Builds a fake OpenAI response shaped like the real SDK's return value."""
    mock_message = MagicMock()
    mock_message.content = json.dumps(extracted_dict)
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


class TestHappyPath:
    @patch("email_parser.client")
    def test_complete_email_parses_successfully(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            {
                "customer_name": "Acme Inc",
                "customer_contact_email": "buyer@acme.com",
                "ae_name": "Jordan Lee",
                "ae_email": "jordan@novacrm.com",
                "salesforce_link": "https://novacrm.lightning.force.com/opp/123",
            }
        )

        deal = parse_deal_email(
            raw_email_text="Hey team, just closed Acme Inc! Contact: buyer@acme.com. SF link: https://novacrm.lightning.force.com/opp/123",
            sender_email="jordan@novacrm.com",
        )

        assert isinstance(deal, ParsedDeal)
        assert deal.customer_name == "Acme Inc"
        assert deal.customer_contact_email == "buyer@acme.com"
        assert deal.ae_name == "Jordan Lee"
        assert deal.salesforce_link == "https://novacrm.lightning.force.com/opp/123"

    @patch("email_parser.client")
    def test_missing_optional_field_still_parses(self, mock_client):
        # salesforce_link is optional — its absence shouldn't block parsing
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            {
                "customer_name": "Beta Corp",
                "customer_contact_email": "ops@betacorp.com",
                "ae_name": "Sam Rivera",
                "ae_email": "sam@novacrm.com",
                "salesforce_link": None,
            }
        )

        deal = parse_deal_email(
            raw_email_text="Closed Beta Corp, contact ops@betacorp.com",
            sender_email="sam@novacrm.com",
        )

        assert deal.customer_name == "Beta Corp"
        assert deal.salesforce_link is None


class TestValidationGuardrail:
    @patch("email_parser.client")
    def test_missing_customer_name_raises(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            {
                "customer_name": None,
                "customer_contact_email": "buyer@acme.com",
                "ae_name": "Jordan Lee",
                "ae_email": "jordan@novacrm.com",
                "salesforce_link": None,
            }
        )

        with pytest.raises(MissingFieldsError) as exc_info:
            parse_deal_email(
                raw_email_text="Hey, signed a deal, contact is buyer@acme.com",
                sender_email="jordan@novacrm.com",
            )
        assert "customer_name" in exc_info.value.missing

    @patch("email_parser.client")
    def test_missing_contact_email_raises(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            {
                "customer_name": "Acme Inc",
                "customer_contact_email": None,
                "ae_name": "Jordan Lee",
                "ae_email": "jordan@novacrm.com",
                "salesforce_link": None,
            }
        )

        with pytest.raises(MissingFieldsError) as exc_info:
            parse_deal_email(
                raw_email_text="Closed Acme Inc, no contact info given",
                sender_email="jordan@novacrm.com",
            )
        assert "customer_contact_email" in exc_info.value.missing

    @patch("email_parser.client")
    def test_completely_malformed_email_raises_with_all_fields_listed(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            {
                "customer_name": None,
                "customer_contact_email": None,
                "ae_name": None,
                "ae_email": None,
                "salesforce_link": None,
            }
        )

        with pytest.raises(MissingFieldsError) as exc_info:
            parse_deal_email(raw_email_text="asdkjf nonsense forwarded chain email", sender_email="")

        assert set(exc_info.value.missing) == {
            "customer_name",
            "customer_contact_email",
            "ae_name",
            "ae_email",
        }
        # The guardrail's whole point: never silently proceed on bad data
        assert exc_info.value.extracted["customer_name"] is None

    @patch("email_parser.client")
    def test_extraction_never_invents_a_plan_tier(self, mock_client):
        # Even if the model gets chatty, plan_tier should never appear —
        # that field is intentionally absent from the schema and prompt.
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            {
                "customer_name": "Acme Inc",
                "customer_contact_email": "buyer@acme.com",
                "ae_name": "Jordan Lee",
                "ae_email": "jordan@novacrm.com",
                "salesforce_link": None,
                "plan_tier": "enterprise",  # model misbehaving, ignore it
            }
        )

        deal = parse_deal_email(
            raw_email_text="Closed Acme Inc, definitely an Enterprise-sized deal",
            sender_email="jordan@novacrm.com",
        )

        assert not hasattr(deal, "plan_tier")