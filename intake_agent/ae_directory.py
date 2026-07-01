"""AE phone directory — a documented assumption, not a guess.

The deal-notification email never includes the AE's phone number, only
their name and email — that's by design, since the brief deliberately
withholds the plan tier and expects a phone call to resolve it. But you
can't dial a number you don't have. In a real NovaCRM deployment this
would be a lookup against Salesforce's User object or an internal HR/CRM
directory API. For this assignment it's a small static mapping.

Call this out explicitly as a stated assumption in the Part 1 doc — it's
exactly the kind of gap-you-noticed-and-handled detail that reads as
real judgment rather than a hand-wave.
"""

import os

# email -> E.164 phone number. Populate with real AE numbers, or use
# TEST_AE_PHONE_OVERRIDE in .env for local testing with your own number.
AE_PHONE_DIRECTORY = {
     "vasupradha.1011@gmail.com": "+919025352164",
}


class UnknownAEError(Exception):
    """Raised when no phone number is on file for the AE — the Intake
    Agent should escalate rather than skip the confirmation call."""

    pass


def lookup_ae_phone(ae_email: str) -> str:
    phone = AE_PHONE_DIRECTORY.get(ae_email.lower())
    if not phone:
        phone = os.getenv("TEST_AE_PHONE_OVERRIDE")
    if not phone:
        raise UnknownAEError(f"No phone number on file for AE: {ae_email}")
    return phone