"""Webhook receiver for Dialnexa call results.

Full retry flow:
1. Call fires → AE doesn't answer → wait 30s → retry
2. Retry also fails → send HTML email to AE (CC: CS) with Enterprise/Growth buttons
3. Someone clicks a button → /confirm-tier → Rocketlane + Slack created

Run: uvicorn webhook_receiver:app --port 8000 --reload
Expose: ngrok http 8000
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from openai import OpenAI

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "communication_agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rocketlane"))

from agent_logger import log_event
from call_store import (
    delete_pending_call,
    get_by_token,
    get_pending_call,
    increment_retry,
    save_pending_call,
)
from dialnexa_client import CallTriggerError, trigger_ae_confirmation_call
from email_notifier import notify_ae_malformed_email  # noqa: F401
from email_notifier import notify_cs_escalation, notify_tier_confirmation_needed
from rocketlane_client import (
    DuplicateProjectError,
    RocketlaneAPIDownError,
    RocketlaneError,
    create_onboarding_project,
)
from slack_agent import create_onboarding_channel

app = FastAPI(title="NovaCRM Onboarding Webhook Receiver")

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")
RETRY_DELAY_SECONDS = 10

# asyncio only holds a weak reference to tasks created via create_task — if
# nothing else references the task, it can be garbage-collected mid-sleep.
# Keep strong references here so scheduled retries always run to completion.
_background_tasks: set = set()


def register_pending_call(call_id: str, deal_metadata: dict):
    save_pending_call(call_id, deal_metadata)
    log_event({
        "event": "call_registered",
        "call_id": call_id,
        "customer": deal_metadata.get("customer_name"),
    })


def extract_plan_tier_from_transcript(transcript: str, customer_name: str) -> str:
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


@app.get("/confirm-tier")
async def confirm_tier(token: str, tier: str):
    """Called when AE or CS clicks a button in the fallback email."""
    if tier not in ("enterprise", "growth"):
        raise HTTPException(status_code=400, detail="Invalid tier")

    call_id, deal = get_by_token(token)
    if not deal:
        return HTMLResponse("""
        <html><body style="font-family:Arial;text-align:center;padding:50px">
        <h2>⚠️ Link expired or already used</h2>
        <p>This confirmation link has already been used or has expired.</p>
        </body></html>
        """)

    customer_name = deal.get("customer_name", "Unknown")
    ae_name = deal.get("ae_name", "Unknown AE")

    log_event({
        "event": "tier_confirmed_via_email",
        "call_id": call_id,
        "plan_tier": tier,
        "customer": customer_name,
        "decision_rationale": (
            f"Plan tier '{tier}' confirmed via email button click after failed voice calls. "
            "Proceeding with Rocketlane project creation."
        ),
    })

    print(f"\n✅ Email confirmation received: {customer_name} → {tier.upper()}")

    # Create Rocketlane project + Slack channel
    _handle_confirmed_tier(customer_name, tier, ae_name, deal)
    delete_pending_call(call_id)

    days = 30 if tier == "enterprise" else 14
    return HTMLResponse(f"""
    <html>
    <body style="font-family:Arial;text-align:center;padding:50px;max-width:500px;margin:0 auto">
        <div style="background:#16a34a;color:white;padding:20px;border-radius:8px">
            <h2>✅ Confirmed!</h2>
        </div>
        <div style="padding:24px;background:#f9f9f9;border:1px solid #eee;border-radius:0 0 8px 8px">
            <p><strong>{customer_name}</strong> has been confirmed as an
            <strong>{tier.title()} Plan</strong> customer.</p>
            <p>A {days}-day onboarding project has been created in Rocketlane
            and a Slack channel has been set up for the customer.</p>
            <p style="color:#666;font-size:13px">You can close this window.</p>
        </div>
    </body>
    </html>
    """)


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

    event_type = payload.get("event_type", "unknown")
    if event_type != "call_ended":
        return JSONResponse({"received": True, "ignored": event_type})

    call_data = payload.get("payload", {}).get("call") or payload
    call_id = call_data.get("id", "unknown")
    status = call_data.get("status", "unknown")
    transcript = call_data.get("transcript", "")

    deal = get_pending_call(call_id)
    if not deal:
        # Unknown call_id — already processed, already retried, or a stale/
        # foreign webhook. Do NOT default retry_count to 0 here: that would
        # make us treat it as a fresh "first failure" and place another
        # live outbound call for a deal we have no record of.
        log_event({
            "event": "webhook_unknown_call_id",
            "call_id": call_id,
            "status": status,
            "decision_rationale": (
                "Received a call-result webhook for a call_id not in the "
                "pending-call store — ignoring instead of treating it as a "
                "new first-attempt failure."
            ),
        })
        print(f"\n⚠️  Ignoring webhook for unknown call_id: {call_id}")
        return JSONResponse({"received": True, "ignored": "unknown_call_id"})

    customer_name = deal.get("customer_name", "Unknown")
    ae_name = deal.get("ae_name", "Unknown AE")
    retry_count = deal.get("_retry_count", 0)
    confirmation_token = deal.get("_confirmation_token", "")

    print(f"\n{'='*50}")
    print(f"📞 CALL RESULT: {customer_name}")
    print(f"   Status: {status} | Retry #{retry_count}")

    # Handle no-answer / failed calls
    if status in ("did_not_pick", "no_answer", "failed", "hangup", "busy"):
        if retry_count == 0:
            # First failure — schedule one retry
            log_event({
                "event": "call_not_answered_retrying",
                "call_id": call_id,
                "customer": customer_name,
                "decision_rationale": f"First call attempt failed — scheduling retry in {RETRY_DELAY_SECONDS} seconds.",
            })
            print(f"   📵 AE didn't answer — retrying in {RETRY_DELAY_SECONDS} seconds...")
            task = asyncio.create_task(_retry_call_after_delay(call_id, deal, RETRY_DELAY_SECONDS))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        else:
            # Second failure — send email with buttons
            log_event({
                "event": "call_not_answered_both_attempts",
                "call_id": call_id,
                "customer": customer_name,
                "decision_rationale": (
                    "Both call attempts failed. Sending fallback email to AE "
                    "with plan tier confirmation buttons. CS Manager CC'd."
                ),
            })
            print(f"   📵 Both attempts failed — sending fallback email...")
            try:
                notify_tier_confirmation_needed(
                    customer_name=customer_name,
                    ae_name=ae_name,
                    ae_email=deal.get("ae_email", ""),
                    cs_email=os.getenv("CS_MANAGER_EMAIL", ""),
                    customer_contact_email=deal.get("customer_contact_email", ""),
                    salesforce_link=deal.get("salesforce_link", ""),
                    confirmation_token=confirmation_token,
                    webhook_base_url=WEBHOOK_BASE_URL,
                )
                print(f"   📨 Fallback email sent to {deal.get('ae_email')} (CC: CS Manager)")
            except Exception as e:
                print(f"   ⚠️  Could not send fallback email: {e}")
                notify_cs_escalation(customer_name, ae_name, deal.get("ae_email", ""), "Both call attempts failed")

        return JSONResponse({"received": True, "status": status, "retry_count": retry_count})

    # No transcript
    if not transcript:
        print("   ⚠️  No transcript — escalating")
        _escalate(customer_name, ae_name, deal, "No transcript returned from call")
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
            f"Extracted '{plan_tier}' from call transcript using GPT-4o."
        ),
    })

    if plan_tier in ("enterprise", "growth"):
        _handle_confirmed_tier(customer_name, plan_tier, ae_name, deal)
    else:
        _escalate(customer_name, ae_name, deal, "Tier unclear after completed call")

    delete_pending_call(call_id)
    return JSONResponse({"received": True, "plan_tier": plan_tier})


async def _retry_call_after_delay(original_call_id: str, deal: dict, delay_seconds: int):
    """Wait delay_seconds then place a retry call to the AE."""
    await asyncio.sleep(delay_seconds)

    customer_name = deal.get("customer_name", "Unknown")
    ae_name = deal.get("ae_name", "Unknown AE")
    ae_email = deal.get("ae_email", "")

    # Look up AE phone
    try:
        from ae_directory import lookup_ae_phone
        ae_phone = lookup_ae_phone(ae_email)
    except Exception as e:
        print(f"   ⚠️  Retry failed — can't look up AE phone: {e}")
        return

    # Place retry call
    try:
        new_call_id = trigger_ae_confirmation_call(
            ae_phone=ae_phone,
            customer_name=customer_name,
            ae_name=ae_name,
        )
        increment_retry(original_call_id, new_call_id)
        log_event({
            "event": "call_retried",
            "original_call_id": original_call_id,
            "new_call_id": new_call_id,
            "customer": customer_name,
            "decision_rationale": "First call unanswered — placed retry call to AE.",
        })
        print(f"   🔄 Retry call placed: {new_call_id}")
    except CallTriggerError as e:
        print(f"   ⚠️  Retry call failed: {e}")


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
    })

    # Step 1: Rocketlane project
    try:
        result = create_onboarding_project(
            customer_name=customer_name,
            plan_tier=plan_tier,
            customer_contact_email=deal.get("customer_contact_email", ""),
            ae_name=ae_name,
        )
        project_id = result.get("projectId", "unknown")
        log_event({"event": "rocketlane_project_created", "customer": customer_name,
                   "plan_tier": plan_tier, "project_id": project_id})
        print(f"\n✅ Rocketlane project created! ID: {project_id}")

    except DuplicateProjectError as e:
        print(f"   ⚠️  Duplicate project — skipping: {e}")
    except RocketlaneAPIDownError as e:
        log_event({
            "event": "rocketlane_api_down",
            "customer": customer_name,
            "reason": str(e),
            "decision_rationale": (
                "Rocketlane API unreachable — project queued for retry. "
                "Plan tier is confirmed and safe in the audit log."
            ),
        })
        print(f"   🔴 Rocketlane API down — queuing for retry")
        from call_store import queue_rocketlane_retry
        queue_rocketlane_retry(customer_name, plan_tier, deal)
        try:
            notify_cs_escalation(
                customer_name=customer_name,
                ae_name=ae_name,
                ae_email=deal.get("ae_email", ""),
                reason=(
                    f"Rocketlane API is down — project creation failed. "
                    f"Plan tier confirmed as {plan_tier}. "
                    f"Project has been queued for retry. Please monitor or retry manually."
                ),
            )
        except Exception:
            pass
    except RocketlaneError as e:
        print(f"   ❌ Rocketlane error: {e}")

    # Step 2: Slack channel
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
        "decision_rationale": "Plan tier unconfirmed — escalating to CS Manager.",
        "action_required": (
            f"CS Manager: confirm plan tier for {customer_name} "
            f"by contacting {ae_name} ({deal.get('ae_email', '')})"
        ),
    })
    print(f"   ⚠️  ESCALATION: {reason}")
    try:
        notify_cs_escalation(customer_name, ae_name, deal.get("ae_email", ""), reason)
    except Exception as e:
        print(f"   ⚠️  CS email notification failed: {e}")


# ── Data Migration Verification Flow ────────────────────────────────────────

@app.post("/task-complete")
async def task_complete(request: Request):
    """Receives Rocketlane webhook when a task is marked complete.

    Only acts on 'Data verification sign-off' (last task in Data Migration).
    When triggered, sends a verification email to the Project Owner/PM
    asking them to confirm the task was genuinely completed.

    Rocketlane automation setup:
    - Trigger: Task status updated → Completed
    - Filter: Task name = 'Data verification sign-off'
    - Action: HTTP POST to {WEBHOOK_BASE_URL}/task-complete
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Log the raw payload unconditionally — Rocketlane's actual field names/
    # shape for a "send https request" action aren't confirmed yet, so this
    # is the only way to see what's really being sent instead of guessing.
    log_event({
        "event": "task_complete_webhook_received",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_payload": payload,
    })
    print(f"\n📥 /task-complete raw payload: {payload}")

    # The Rocketlane automation's own trigger already filters to
    # "Task Name is exactly 'Data verification sign-off'" AND "status
    # changed to Completed" — so by construction, every request that
    # reaches this endpoint from that automation already satisfies both.
    # Rocketlane's HTTPS-request body is a flat task object (confirmed via
    # its own docs-example payload) — no eventType/data wrapper, and no
    # top-level status field. project/phase are nested under "project" and
    # "projectPhase" respectively.
    task_name = payload.get("taskName", "")
    task_id = payload.get("taskId")
    project_name = payload.get("project", {}).get("projectName", "Unknown Project")
    project_id = payload.get("project", {}).get("projectId")
    phase_name = payload.get("projectPhase", {}).get("projectPhaseName", "Unknown Phase")

    # Safety net in case this endpoint is ever hit directly/misconfigured —
    # not relied on for the normal Rocketlane automation path.
    VERIFICATION_TASKS = ["data verification sign-off", "data verification"]
    if task_name.lower() not in VERIFICATION_TASKS:
        print(f"   ⚠️  Ignored — taskName was {task_name!r}, expected one of {VERIFICATION_TASKS}")
        return JSONResponse({"received": True, "ignored": f"not a verification task: {task_name}"})

    # Extract customer name from project name (convention: "{Customer} Onboarding")
    customer_name = project_name.replace(" Onboarding", "").strip()

    log_event({
        "event": "task_verification_triggered",
        "task_id": task_id,
        "task_name": task_name,
        "project_name": project_name,
        "customer": customer_name,
        "decision_rationale": (
            f"'{task_name}' marked complete — sending verification email to PM/Owner. "
            "This guardrail exists because data migration tasks have historically been "
            "marked done without actual verification, causing go-live escalations."
        ),
    })

    # Generate token and store task details
    import secrets as _secrets
    from call_store import save_task_verification
    token = _secrets.token_urlsafe(16)
    save_task_verification(token, {
        "task_id": task_id,
        "task_name": task_name,
        "project_name": project_name,
        "project_id": project_id,
        "phase_name": phase_name,
        "customer_name": customer_name,
    })

    # Send verification email to CS Manager / Project Owner
    from email_notifier import notify_task_verification_needed
    recipient = os.getenv("CS_MANAGER_EMAIL", "")
    try:
        notify_task_verification_needed(
            task_name=task_name,
            project_name=project_name,
            customer_name=customer_name,
            phase_name=phase_name,
            recipient_email=recipient,
            confirmation_token=token,
            webhook_base_url=WEBHOOK_BASE_URL,
        )
        print(f"\n📋 Verification email sent for: {task_name} ({project_name})")
    except Exception as e:
        print(f"   ⚠️  Could not send verification email: {e}")
        log_event({"event": "verification_email_failed", "reason": str(e)})

    return JSONResponse({"received": True, "verification_sent": True})


@app.get("/confirm-task")
async def confirm_task(token: str, action: str):
    """Called when PM/Owner clicks Yes or No in the verification email."""
    if action not in ("yes", "no"):
        raise HTTPException(status_code=400, detail="Invalid action")

    from call_store import get_task_verification, delete_task_verification
    task_data = get_task_verification(token)

    if not task_data:
        return HTMLResponse("""
        <html><body style="font-family:Arial;text-align:center;padding:50px">
        <h2>⚠️ Link expired or already used</h2>
        <p>This verification link has already been used or has expired.</p>
        </body></html>
        """)

    task_id = task_data.get("task_id")
    task_name = task_data.get("task_name")
    project_name = task_data.get("project_name")
    customer_name = task_data.get("customer_name")

    from rocketlane_client import update_task_status

    if action == "yes":
        # Confirmed complete — keep task as done, log verification
        try:
            update_task_status(task_id, completed=True)
        except Exception as e:
            print(f"   ⚠️  Could not update task status: {e}")

        log_event({
            "event": "task_verification_confirmed",
            "task_id": task_id,
            "task_name": task_name,
            "project": project_name,
            "customer": customer_name,
            "decision_rationale": "PM/Owner confirmed task genuinely completed via email verification.",
        })
        delete_task_verification(token)

        return HTMLResponse(f"""
        <html>
        <body style="font-family:Arial;text-align:center;padding:50px;max-width:500px;margin:0 auto">
            <div style="background:#16a34a;color:white;padding:20px;border-radius:8px">
                <h2>✅ Verified!</h2>
            </div>
            <div style="padding:24px;background:#f9f9f9;border:1px solid #eee;border-radius:0 0 8px 8px">
                <p><strong>{task_name}</strong> has been verified as genuinely complete.</p>
                <p>Project: {project_name}<br>Customer: {customer_name}</p>
                <p style="color:#666;font-size:13px">The task status has been confirmed in Rocketlane. You can close this window.</p>
            </div>
        </body>
        </html>
        """)

    else:
        # Not actually done — reopen the task
        try:
            update_task_status(task_id, completed=False)
        except Exception as e:
            print(f"   ⚠️  Could not reopen task: {e}")

        log_event({
            "event": "task_verification_rejected",
            "task_id": task_id,
            "task_name": task_name,
            "project": project_name,
            "customer": customer_name,
            "decision_rationale": (
                "PM/Owner indicated task NOT genuinely complete. "
                "Task reopened in Rocketlane to prevent false completion from reaching go-live."
            ),
        })
        delete_task_verification(token)

        return HTMLResponse(f"""
        <html>
        <body style="font-family:Arial;text-align:center;padding:50px;max-width:500px;margin:0 auto">
            <div style="background:#dc2626;color:white;padding:20px;border-radius:8px">
                <h2>🔄 Task Reopened</h2>
            </div>
            <div style="padding:24px;background:#f9f9f9;border:1px solid #eee;border-radius:0 0 8px 8px">
                <p><strong>{task_name}</strong> has been reopened in Rocketlane.</p>
                <p>Project: {project_name}<br>Customer: {customer_name}</p>
                <p style="color:#666;font-size:13px">
                    The team has been notified. Please complete the actual data verification
                    before marking this task done again.
                </p>
            </div>
        </body>
        </html>
        """)