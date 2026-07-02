"""Gmail inbox poller (IMAP) — the trigger for the Intake & Routing Agent.

Uses IMAP with an app-specific password rather than the full Gmail API
OAuth flow — dramatically faster to set up for a project like this, no
consent screens or scopes. For the written doc's "what changes in
production" note: a real deployment would more likely use Gmail API push
notifications via Pub/Sub for instant triggering instead of polling.
"""

import email
import imaplib
import os
import time
from email.header import decode_header

from dotenv import load_dotenv

load_dotenv()

from ae_directory import UnknownAEError, lookup_ae_phone
from agent_logger import log_event
from call_store import save_pending_call
from dialnexa_client import CallTriggerError, escalate_to_human, trigger_ae_confirmation_call
from email_parser import MissingFieldsError, parse_deal_email

IMAP_HOST = "imap.gmail.com"
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
SUBJECT_FILTER = os.getenv("DEAL_EMAIL_SUBJECT_FILTER", "New Deal")


def connect():
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD"))
    return imap


def fetch_unseen_deal_emails(imap):
    imap.select("INBOX")
    # Server-side filtering — only fetches emails matching subject
    # avoids scanning thousands of unread emails on every poll
    status, data = imap.search(None, f'UNSEEN SUBJECT "{SUBJECT_FILTER}"')
    if status != "OK" or not data[0]:
        return []

    results = []
    for num in data[0].split():
        status, msg_data = imap.fetch(num, "(RFC822)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            try:
                subject = subject.decode(encoding or "utf-8")
            except (LookupError, UnicodeDecodeError):
                subject = subject.decode("utf-8", errors="ignore")

        sender = email.utils.parseaddr(msg["From"])[1]
        body = _extract_body(msg)
        results.append({"subject": subject, "sender": sender, "body": body})

    return results


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
    return msg.get_payload(decode=True).decode(errors="ignore")


def handle_new_deal(deal):
    """Full pipeline for a validated deal email:
    1. Look up AE phone number
    2. Trigger Dialnexa call to confirm plan tier
    3. Save call metadata so webhook_receiver can match the result
    4. If anything fails, escalate to human instead of guessing
    """
    print(f"\n🎯 New deal: {deal.customer_name} (AE: {deal.ae_name})")

    try:
        ae_phone = lookup_ae_phone(deal.ae_email)
    except UnknownAEError as e:
        escalate_to_human(deal.customer_name, deal.ae_name, deal.ae_email, reason=str(e))
        return

    try:
        call_id = trigger_ae_confirmation_call(
            ae_phone=ae_phone,
            customer_name=deal.customer_name,
            ae_name=deal.ae_name,
        )
    except CallTriggerError as e:
        escalate_to_human(
            deal.customer_name, deal.ae_name, deal.ae_email,
            reason=f"Call could not be placed: {e}"
        )
        return

    # Save to file-based store so webhook_receiver (separate process) can read it
    save_pending_call(call_id, {
        "customer_name": deal.customer_name,
        "customer_contact_email": deal.customer_contact_email,
        "ae_name": deal.ae_name,
        "ae_email": deal.ae_email,
        "salesforce_link": deal.salesforce_link,
    })

    log_event({
        "event": "call_triggered",
        "call_id": call_id,
        "customer": deal.customer_name,
        "ae": deal.ae_name,
    })

    print(f"📞 Call triggered (ID: {call_id}) — waiting for AE to pick up...")


def run_poll_loop():
    imap = connect()
    log_event({"event": "poller_started", "subject_filter": SUBJECT_FILTER})
    print(f"👀 Watching inbox for emails with '{SUBJECT_FILTER}' in subject...")
    print(f"   Polling every {POLL_INTERVAL_SECONDS}s\n")

    try:
        while True:
            for raw in fetch_unseen_deal_emails(imap):
                log_event({
                    "event": "email_received",
                    "sender": raw["sender"],
                    "subject": raw["subject"],
                })
                print(f"\n📧 New email: '{raw['subject']}' from {raw['sender']}")

                try:
                    deal = parse_deal_email(raw["body"], raw["sender"])
                    log_event({
                        "event": "email_parsed",
                        "customer": deal.customer_name,
                        "ae": deal.ae_name,
                    })
                    handle_new_deal(deal)

                except MissingFieldsError as e:
                    log_event({
                        "event": "validation_failed",
                        "missing_fields": e.missing,
                        "extracted_so_far": e.extracted,
                        "decision_rationale": (
                            "Required fields missing — flagging for clarification "
                            "instead of guessing."
                        ),
                    })
                    print(f"❌ Validation failed — missing: {e.missing}")
                    print("   Not proceeding. AE must resend with complete info.")
                    try:
                        from email_notifier import notify_ae_malformed_email
                        notify_ae_malformed_email(raw["sender"], e.missing)
                    except Exception as email_err:
                        print(f"   Could not notify AE: {email_err}")

            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        imap.logout()


if __name__ == "__main__":
    run_poll_loop()