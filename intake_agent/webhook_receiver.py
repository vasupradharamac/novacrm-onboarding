"""Webhook receiver for Dialnexa call results.

Dialnexa POSTs here when a call ends. We extract the plan tier from
the raw transcript using OpenAI, then create a Rocketlane project and
a Slack channel via the Communication Agent.

Run this with: uvicorn webhook_receiver:app --port 8000 --reload
Then expose it: ngrok http 8000
"""

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from openai import OpenAI

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "communication_agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rocketlane"))

from agent_logger import log_event
from call_store import delete_pending_call, get_pending_call
from rocketlane_client import (
    DuplicateProjectError,
    RocketlaneAPIDownError,
    RocketlaneError,
    create_onboarding_project,
)
from slack_agent import create_onboarding_channel

app = FastAPI(title="NovaCRM Onboarding Webhook Receiver")


def register_pending_call(call_id: str, deal_metadata: dict):
    """Called by poller.py after triggering a Dialnexa call."""
    from call_store import save_pending_call
    save_pending_call(call_id, deal_metadata)
    log_event({
        "event": "call_registered",
        "call_id": call_id,
        "customer": deal_metadata.get("customer_name"),
    })


def extract_plan_tier_from_transcript(transcript: str, customer_name: str) -> str:
    """Use OpenAI to extract the plan tier from the call transcript.

    Returns 'enterprise', 'growth', or 'unclear'.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=10,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract plan tier confirmations from call transcripts. "
                    "Return ONLY one of these three words: enterprise, growth, unclear. "
                    "Return 'enterprise' if the AE clearly confirmed Enterprise plan. "
                    "Return 'growth' if the AE clearly confirmed Growth plan. "
                    "Return 'unclear' if the AE was ambiguous, didn't answer, "
                    "or the call failed. Never return anything other than these three words."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Call transcript for {customer_name} onboarding confirmation:\n\n"
                    f"{transcript}\n\n"
                    "What plan tier did the AE confirm?"
                ),
            },
        ],
    )

    result = response.choices[0].message.content.strip().lower()
    if result not in ("enterprise", "growth", "unclear"):
        return "unclear"
    return result


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/call-result")
async def call_result(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    log_event({
        "event": "webhook_received",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_payload": payload,
    })

    # Only process call_ended events
    event_type = payload.get("event_type", "unknown")
    if event_type != "call_ended":
        return JSONResponse({"received": True, "ignored": event_type})

    call_data = payload.get("payload", {}).get("call") or payload
    call_id = call_data.get("id", "unknown")
    status = call_data.get("status", "unknown")
    transcript = call_data.get("transcript", "")

    deal = get_pending_call(call_id)
    customer_name = deal.get("customer_name", "Unknown")
    ae_name = deal.get("ae_name", "Unknown AE")

    print(f"\n{'='*50}")
    print(f"📞 CALL RESULT: {customer_name}")
    print(f"   Status: {status}")
    print(f"   Transcript preview: {str(transcript)[:150]}...")

    # Handle no-answer / failed calls
    if status in ("did_not_pick", "no_answer", "failed", "hangup", "busy"):
        log_event({
            "event": "call_not_answered",
            "call_id": call_id,
            "status": status,
            "customer": customer_name,
            "decision_rationale": (
                f"Call status was '{status}' — AE did not answer. "
                "Escalating to CS Manager instead of guessing tier."
            ),
        })
        print(f"   📵 AE did not answer — escalating")
        _escalate(customer_name, ae_name, deal, reason=f"AE did not answer (status: {status})")
        delete_pending_call(call_id)
        return JSONResponse({"received": True, "status": status})

    # No transcript
    if not transcript:
        print("   ⚠️  No transcript — escalating")
        _escalate(customer_name, ae_name, deal, reason="No transcript returned from call")
        delete_pending_call(call_id)
        return JSONResponse({"received": True, "plan_tier": "unclear"})

    # Extract plan tier from transcript
    print(f"   🔍 Extracting tier from transcript...")
    plan_tier = extract_plan_tier_from_transcript(str(transcript), customer_name)
    print(f"   ✅ Plan tier confirmed: {plan_tier.upper()}")

    log_event({
        "event": "plan_tier_extracted",
        "call_id": call_id,
        "plan_tier": plan_tier,
        "customer": customer_name,
        "ae": ae_name,
        "decision_rationale": (
            f"Extracted '{plan_tier}' from call transcript using GPT-4o. "
            "Transcript-based extraction used for reliability and auditability."
        ),
    })

    if plan_tier in ("enterprise", "growth"):
        _handle_confirmed_tier(customer_name, plan_tier, ae_name, deal)
    else:
        _escalate(customer_name, ae_name, deal, reason="Tier unclear after completed call")

    delete_pending_call(call_id)
    return JSONResponse({"received": True, "plan_tier": plan_tier})


def _handle_confirmed_tier(
    customer_name: str, plan_tier: str, ae_name: str, deal: dict
):
    days = 30 if plan_tier == "enterprise" else 14
    csm_type = "dedicated CSM" if plan_tier == "enterprise" else "pooled CSM"

    log_event({
        "event": "rocketlane_project_creating",
        "customer": customer_name,
        "plan_tier": plan_tier,
        "onboarding_days": days,
        "csm_type": csm_type,
        "decision_rationale": (
            f"Plan tier confirmed as {plan_tier} by {ae_name} via voice call. "
            f"Using {days}-day template with {csm_type}."
        ),
    })

    # Step 1: Create Rocketlane project
    try:
        result = create_onboarding_project(
            customer_name=customer_name,
            plan_tier=plan_tier,
            customer_contact_email=deal.get("customer_contact_email", ""),
            ae_name=ae_name,
        )
        project_id = result.get("projectId", "unknown")
        log_event({
            "event": "rocketlane_project_created",
            "customer": customer_name,
            "plan_tier": plan_tier,
            "project_id": project_id,
        })
        print(f"\n✅ Rocketlane project created! ID: {project_id}")

    except DuplicateProjectError as e:
        log_event({"event": "rocketlane_duplicate_project", "customer": customer_name, "reason": str(e)})
        print(f"   ⚠️  Duplicate project — skipping: {e}")

    except RocketlaneAPIDownError as e:
        log_event({"event": "rocketlane_api_down", "customer": customer_name, "reason": str(e)})
        print(f"   🔴 Rocketlane API down: {e}")

    except RocketlaneError as e:
        log_event({"event": "rocketlane_error", "customer": customer_name, "reason": str(e)})
        print(f"   ❌ Rocketlane error: {e}")

    # Step 2: Create Slack channel (runs regardless of Rocketlane outcome)
    try:
        create_onboarding_channel(
            customer_name=customer_name,
            plan_tier=plan_tier,
            customer_contact_email=deal.get("customer_contact_email", ""),
        )
    except Exception as e:
        print(f"   ⚠️  Slack channel creation failed: {e}")
        log_event({"event": "slack_error", "customer": customer_name, "reason": str(e)})


def _escalate(customer_name: str, ae_name: str, deal: dict, reason: str):
    log_event({
        "event": "escalation_required",
        "customer": customer_name,
        "ae": ae_name,
        "reason": reason,
        "decision_rationale": (
            "Plan tier could not be confirmed — escalating to CS Manager. "
            "Never guessing tier to avoid wrong template being used."
        ),
        "action_required": (
            f"CS Manager: manually confirm plan tier for {customer_name} "
            f"by contacting {ae_name} ({deal.get('ae_email', '')})"
        ),
    })
    print(f"   ⚠️  ESCALATION: {reason}")
    print(f"   CS Manager must confirm tier for {customer_name}")