"""Tests for the rule-based email parser.

No mocks needed — pure regex, runs instantly, costs nothing.
"""

import pytest
from email_parser import MissingFieldsError, ParsedDeal, parse_deal_email

GOOD_EMAIL = """Hi team,

Excited to share that we just closed a deal with Google LLC!

Customer: Google LLC
Customer Contact: sarah.chen@google.com
Salesforce Opportunity: https://novacrm.lightning.force.com/lightning/r/Opportunity/0061234567GOOGLE/view

Please kick off the onboarding process at your earliest.

Thanks,
Jordan Lee
Account Executive, NovaCRM
jordan.lee@novacrm.com
"""

SENDER = "jordan.lee@novacrm.com"


class TestHappyPath:
    def test_complete_email_parses_successfully(self):
        deal = parse_deal_email(GOOD_EMAIL, SENDER)
        assert isinstance(deal, ParsedDeal)
        assert deal.customer_name == "Google LLC"
        assert deal.customer_contact_email == "sarah.chen@google.com"
        assert deal.ae_name == "Jordan Lee"
        assert deal.ae_email == SENDER
        assert "GOOGLE" in deal.salesforce_link

    def test_ae_email_always_comes_from_sender(self):
        deal = parse_deal_email(GOOD_EMAIL, SENDER)
        assert deal.ae_email == SENDER

    def test_salesforce_link_optional(self):
        email_no_sf = GOOD_EMAIL.replace(
            "Salesforce Opportunity: https://novacrm.lightning.force.com/lightning/r/Opportunity/0061234567GOOGLE/view\n",
            ""
        )
        deal = parse_deal_email(email_no_sf, SENDER)
        assert deal.salesforce_link is None

    def test_ae_name_from_signature(self):
        deal = parse_deal_email(GOOD_EMAIL, SENDER)
        assert "Jordan" in deal.ae_name

    def test_ae_name_fallback_to_email_prefix(self):
        email_no_sig = "Customer: Acme Inc\nCustomer Contact: bob@acme.com\n"
        deal = parse_deal_email(email_no_sig, "jane.smith@novacrm.com")
        assert deal.ae_name == "Jane Smith"


class TestValidationGuardrail:
    def test_missing_customer_name_raises(self):
        email = "Customer Contact: sarah@google.com\n\nThanks,\nJordan"
        with pytest.raises(MissingFieldsError) as exc:
            parse_deal_email(email, SENDER)
        assert "customer_name" in exc.value.missing

    def test_missing_contact_email_raises(self):
        email = "Customer: Google LLC\n\nThanks,\nJordan"
        with pytest.raises(MissingFieldsError) as exc:
            parse_deal_email(email, SENDER)
        assert "customer_contact_email" in exc.value.missing

    def test_completely_malformed_raises_all_fields(self):
        with pytest.raises(MissingFieldsError) as exc:
            parse_deal_email("asdfjkl nonsense", SENDER)
        assert "customer_name" in exc.value.missing
        assert "customer_contact_email" in exc.value.missing

    def test_no_plan_tier_ever_extracted(self):
        deal = parse_deal_email(GOOD_EMAIL, SENDER)
        assert not hasattr(deal, "plan_tier")

    def test_contact_email_fallback_any_non_sender_email(self):
        email = "Customer: Acme Inc\n\nPlease contact bob@acme.com for onboarding.\n\nThanks,\nJordan"
        deal = parse_deal_email(email, SENDER)
        assert deal.customer_contact_email == "bob@acme.com"