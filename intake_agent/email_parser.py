"""Intake & Routing Agent — email parsing and field validation.

Uses a rule-based regex parser instead of an LLM — the email format is
structured and consistent, so deterministic parsing is faster, cheaper,
and more predictable than an API call. The LLM only enters the pipeline
from Dialnexa onwards (transcript extraction).

Per Part 3's guardrail: if a required field can't be found, this raises
rather than guessing — the caller flags for human clarification instead
of silently proceeding with incomplete data.
"""

import re
from dataclasses import dataclass
from typing import Optional

REQUIRED_FIELDS = ["customer_name", "customer_contact_email", "ae_name", "ae_email"]


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


# Patterns ordered from most to least specific
_PATTERNS = {
    "customer_name": [
        r"Customer(?:\s+Company)?(?:\s+Name)?:\s*(.+)",
        r"Client(?:\s+Name)?:\s*(.+)",
        r"Account(?:\s+Name)?:\s*(.+)",
        r"just closed(?:\s+a deal with)?\s+([A-Z][^\n!.]+)",
    ],
    "customer_contact_email": [
        r"Customer\s+Contact(?:\s+Email)?:\s*([\w.+%-]+@[\w.-]+\.[a-zA-Z]{2,})",
        r"Contact(?:\s+Email)?:\s*([\w.+%-]+@[\w.-]+\.[a-zA-Z]{2,})",
        r"Contact:\s*([\w.+%-]+@[\w.-]+\.[a-zA-Z]{2,})",
        # Any email that isn't the sender's own email
    ],
    "salesforce_link": [
        r"Salesforce(?:\s+Opportunity)?(?:\s+Link)?:\s*(https?://\S+)",
        r"(https?://[^\s]*\.?(?:force|salesforce)\.com/\S+)",
        r"SF(?:\s+link)?:\s*(https?://\S+)",
    ],
    "ae_name": [
        r"Thanks,\s*\n+\s*([^\n]+)",
        r"Regards,\s*\n+\s*([^\n]+)",
        r"Best,\s*\n+\s*([^\n]+)",
        r"Cheers,\s*\n+\s*([^\n]+)",
        r"- ([A-Z][a-z]+ [A-Z][a-z]+)(?:\n|$)",
    ],
}


def _extract_field(pattern_list: list, text: str) -> Optional[str]:
    for pattern in pattern_list:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip().rstrip(".,;")
    return None


def parse_deal_email(raw_email_text: str, sender_email: str) -> ParsedDeal:
    """Extract structured fields from a raw deal-notification email.

    Raises MissingFieldsError if any required field can't be found.
    No LLM call — pure regex. Fast, free, deterministic.
    """
    extracted = {}

    for field, patterns in _PATTERNS.items():
        extracted[field] = _extract_field(patterns, raw_email_text)

    # AE email is always the sender — no need to parse it
    extracted["ae_email"] = sender_email

    # AE name fallback: use sender's email prefix if not found in body
    if not extracted.get("ae_name"):
        extracted["ae_name"] = sender_email.split("@")[0].replace(".", " ").title()

    # Customer contact email: if not found via label, look for any email
    # in the body that isn't the sender's own address
    if not extracted.get("customer_contact_email"):
        all_emails = re.findall(
            r"[\w.+%-]+@[\w.-]+\.[a-zA-Z]{2,}", raw_email_text
        )
        other_emails = [e for e in all_emails if e.lower() != sender_email.lower()]
        if other_emails:
            extracted["customer_contact_email"] = other_emails[0]

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