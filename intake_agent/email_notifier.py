"""Email notifier — sends escalation and bounceback emails via SMTP.

Uses the same Gmail account (GMAIL_ADDRESS) that the poller watches,
so no new credentials are needed. Sends:
- CS escalation emails to CS_MANAGER_EMAIL when a call fails or tier is unclear
- Bounceback emails to the AE when their deal email is malformed
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()


def _send_email(to: str, subject: str, body: str):
    """Send an email via Gmail SMTP."""
    gmail_address = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, to, msg.as_string())

    print(f"   📨 Email sent to {to}: {subject}")


def notify_cs_escalation(
    customer_name: str,
    ae_name: str,
    ae_email: str,
    reason: str,
):
    """Notify the CS Manager when a call fails or tier is unclear."""
    cs_email = os.getenv("CS_MANAGER_EMAIL")
    if not cs_email:
        print("   ⚠️  CS_MANAGER_EMAIL not set — skipping email notification")
        return

    subject = f"[NovaCRM] Action Required: Onboarding tier unconfirmed for {customer_name}"
    body = f"""Hi Priya,

The automated onboarding pipeline was unable to confirm the plan tier for a new customer and requires your attention.

Customer: {customer_name}
Account Executive: {ae_name} ({ae_email})
Reason: {reason}

Action Required:
Please contact {ae_name} directly to confirm whether {customer_name} is on the Enterprise (30-day, dedicated CSM) or Growth (14-day, pooled CSM) plan, then manually trigger project creation in Rocketlane.

This escalation was logged in the audit trail with full context.

— NovaCRM Onboarding Agent
"""
    try:
        _send_email(cs_email, subject, body)
    except Exception as e:
        print(f"   ⚠️  Failed to send CS escalation email: {e}")


def notify_ae_malformed_email(
    ae_email: str,
    missing_fields: list,
):
    """Notify the AE their deal email was missing required fields."""
    subject = "[NovaCRM] Deal notification incomplete — please resend"
    body = f"""Hi,

Your deal notification email was received but could not be processed because the following required fields were missing or unclear:

Missing fields: {', '.join(missing_fields)}

Please resend your deal notification with the following information:
- Customer company name
- Customer contact email
- Your name and email
- Salesforce opportunity link (optional but recommended)

Example:
  Customer: Acme Inc
  Customer Contact: john@acme.com
  Salesforce: https://novacrm.lightning.force.com/...

— NovaCRM Onboarding Agent
"""
    try:
        _send_email(ae_email, subject, body)
    except Exception as e:
        print(f"   ⚠️  Failed to send AE bounceback email: {e}")