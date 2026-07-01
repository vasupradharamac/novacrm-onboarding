"""Dialnexa voice call client.

Triggers an outbound AI call to the AE to confirm the plan tier.
Handles failure gracefully — if the call can't be placed it escalates
to a human instead of silently failing or guessing the tier.

API ref: https://dialnexa.com/docs/api-reference/calls/create-outbound
"""

import os
import httpx
from dotenv import load_dotenv
from agent_logger import log_event

load_dotenv()

DIALNEXA_BASE_URL = "https://api.dialnexa.com"


class CallTriggerError(Exception):
    """Raised when the call couldn't be placed — triggers human escalation."""
    pass


def trigger_ae_confirmation_call(ae_phone: str, customer_name: str, ae_name: str) -> str:
    """Trigger a Dialnexa outbound call to the AE to confirm the plan tier.

    Returns the call_id on success.
    Raises CallTriggerError on failure so the caller can escalate.

    ae_phone must be in E.164 format e.g. +919876543210
    """
    api_key = os.getenv("DIALNEXA_API_KEY")
    agent_id = os.getenv("DIALNEXA_AGENT_ID")

    if not api_key:
        raise CallTriggerError("DIALNEXA_API_KEY not set")
    if not agent_id:
        raise CallTriggerError("DIALNEXA_AGENT_ID not set")

    # Ensure E.164 format — add + if missing
    if not ae_phone.startswith("+"):
        ae_phone = f"+{ae_phone}"

    payload = {
        "agent_id": agent_id,
        "phone_number": ae_phone,
        "metadata": {
            "customer_name": customer_name,
            "ae_name": ae_name,
        }
    }

    print(f"DEBUG payload being sent: {payload}")
    print(f"DEBUG phone type: {type(ae_phone)}")

    try:
        response = httpx.post(
            f"{DIALNEXA_BASE_URL}/calls",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )

        print(f"DEBUG response: {response.status_code} {response.text}")

        if response.status_code in (200, 201):
            data = response.json()
            call_id = data.get("id") or data.get("call_id", "unknown")
            log_event({
                "event": "call_triggered",
                "call_id": call_id,
                "customer": customer_name,
                "ae": ae_name,
                "ae_phone": ae_phone,
                "status": data.get("status"),
            })
            return call_id
        else:
            raise CallTriggerError(
                f"Dialnexa API returned {response.status_code}: {response.text}"
            )

    except httpx.TimeoutException:
        raise CallTriggerError("Dialnexa API timed out")
    except httpx.RequestError as e:
        raise CallTriggerError(f"Network error calling Dialnexa: {e}")


def escalate_to_human(customer_name: str, ae_name: str, ae_email: str, reason: str):
    """Log a human-escalation event when the call can't be placed."""
    log_event({
        "event": "human_escalation_required",
        "customer": customer_name,
        "ae_name": ae_name,
        "ae_email": ae_email,
        "reason": reason,
        "decision_rationale": (
            "Could not complete automated AE call to confirm plan tier. "
            "Escalating to CS Manager to confirm manually before creating project."
        ),
        "action_required": f"Please confirm plan tier for {customer_name} by contacting {ae_name} ({ae_email})",
    })
    print(f"\n⚠️  ESCALATION: Cannot place call for {customer_name}. Reason: {reason}")
    print(f"   CS Manager: please confirm plan tier with {ae_name} ({ae_email})\n")