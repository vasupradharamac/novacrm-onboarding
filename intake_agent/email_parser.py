"""Intake & Routing Agent — email parsing and field validation.

Takes the raw text of a "new deal" email an AE sends to the CS inbox and
extracts the structured fields needed to kick off onboarding. Per Part 3's
guardrail: if a required field can't be found, this raises rather than
guessing — the caller is expected to flag for human clarification instead
of silently proceeding with incomplete data.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv()

# client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

REQUIRED_FIELDS = ["customer_name", "customer_contact_email", "ae_name", "ae_email"]

EXTRACTION_SYSTEM_PROMPT = """You extract structured deal-notification data from
an email an Account Executive sends to a CS team inbox when a new deal closes.

Return ONLY a JSON object with these keys (use null for anything not present
in the email — never invent a value):
- customer_name: the company/customer name
- customer_contact_email: the customer's contact email, if mentioned
- ae_name: the Account Executive's name (usually the sender)
- ae_email: the Account Executive's email (usually the sender's email)
- salesforce_link: the Salesforce opportunity URL, if present

Do not guess the plan tier — it is never in this email by design and is
confirmed later via phone call. Do not include a plan_tier key at all."""


class MissingFieldsError(Exception):
    """Raised when a required field couldn't be extracted from the email."""

    def __init__(self, missing: list, extracted: dict):
        self.missing = missing
        self.extracted = extracted
        super().__init__(f"Missing required fields: {missing}")


@dataclass
class ParsedDeal:
    customer_name: str
    customer_contact_email: str
    ae_name: str
    ae_email: str
    salesforce_link: Optional[str] = None


def parse_deal_email(raw_email_text: str, sender_email: str) -> ParsedDeal:
    """Extract structured fields from a raw deal-notification email.

    Raises MissingFieldsError if any required field can't be found.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Sender email: {sender_email}\n\nEmail body:\n{raw_email_text}",
            },
        ],
    )
    extracted = json.loads(response.choices[0].message.content)

    missing = [f for f in REQUIRED_FIELDS if not extracted.get(f)]
    if missing:
        raise MissingFieldsError(missing, extracted)

    return ParsedDeal(
        customer_name=extracted["customer_name"],
        customer_contact_email=extracted["customer_contact_email"],
        ae_name=extracted["ae_name"],
        ae_email=extracted["ae_email"],
        salesforce_link=extracted.get("salesforce_link"),
    )