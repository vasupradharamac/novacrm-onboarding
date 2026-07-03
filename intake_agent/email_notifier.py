"""Email notifier — CS escalations and AE bouncebacks via SMTP.

Sends:
- HTML email with Enterprise/Growth buttons when both call attempts fail
- Plain text bounceback to AE when their deal email is malformed
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()


def _send_email(to: str, subject: str, body_plain: str, body_html: str = None, cc: str = None):
    gmail_address = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"NovaCRM Onboarding Agent <{gmail_address}>"
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    msg.attach(MIMEText(body_plain, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    recipients = [to] + ([cc] if cc else [])

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, recipients, msg.as_string())

    print(f"   📨 Email sent to {to}" + (f" (CC: {cc})" if cc else ""))


def notify_tier_confirmation_needed(
    customer_name: str,
    ae_name: str,
    ae_email: str,
    cs_email: str,
    customer_contact_email: str,
    salesforce_link: str,
    confirmation_token: str,
    webhook_base_url: str,
):
    """Send HTML email with Enterprise/Growth buttons when both calls fail."""

    enterprise_url = f"{webhook_base_url}/confirm-tier?token={confirmation_token}&tier=enterprise"
    growth_url = f"{webhook_base_url}/confirm-tier?token={confirmation_token}&tier=growth"

    subject = f"[NovaCRM] Action Required: Confirm plan tier for {customer_name}"

    body_plain = f"""Hi {ae_name},

Our automated system attempted to reach you twice to confirm the plan tier for {customer_name} but was unable to connect.

Customer: {customer_name}
Customer Contact: {customer_contact_email}
Salesforce: {salesforce_link or 'N/A'}

Please confirm the plan tier by clicking one of the links below:

Enterprise Plan (30-day, dedicated CSM):
{enterprise_url}

Growth Plan (14-day, pooled CSM):
{growth_url}

Once you click, the onboarding project will be created automatically.

— NovaCRM Onboarding Agent
"""

    body_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: #1a1a2e; padding: 20px; border-radius: 8px 8px 0 0;">
        <h2 style="color: white; margin: 0;">NovaCRM Onboarding</h2>
        <p style="color: #aaa; margin: 5px 0 0 0;">Action Required</p>
    </div>

    <div style="background: #f9f9f9; padding: 24px; border: 1px solid #eee;">
        <p>Hi <strong>{ae_name}</strong>,</p>

        <p>Our automated system attempted to reach you <strong>twice</strong> to confirm the plan tier for the following customer but was unable to connect:</p>

        <div style="background: white; border: 1px solid #ddd; border-radius: 6px; padding: 16px; margin: 16px 0;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 6px 0; color: #666; width: 40%;">Customer</td>
                    <td style="padding: 6px 0;"><strong>{customer_name}</strong></td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #666;">Contact</td>
                    <td style="padding: 6px 0;">{customer_contact_email}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #666;">Salesforce</td>
                    <td style="padding: 6px 0;"><a href="{salesforce_link or '#'}">{salesforce_link or 'N/A'}</a></td>
                </tr>
            </table>
        </div>

        <p>Please confirm the plan tier by clicking one of the buttons below. The onboarding project will be created automatically once you do.</p>

        <div style="text-align: center; margin: 32px 0;">
            <a href="{enterprise_url}"
               style="background: #2563eb; color: white; padding: 14px 28px; border-radius: 6px;
                      text-decoration: none; font-weight: bold; margin: 0 8px; display: inline-block;">
                🏢 Enterprise Plan
                <br><small style="font-weight: normal; opacity: 0.9;">30-day · Dedicated CSM</small>
            </a>
            <a href="{growth_url}"
               style="background: #16a34a; color: white; padding: 14px 28px; border-radius: 6px;
                      text-decoration: none; font-weight: bold; margin: 0 8px; display: inline-block;">
                🌱 Growth Plan
                <br><small style="font-weight: normal; opacity: 0.9;">14-day · Pooled CSM</small>
            </a>
        </div>

        <p style="color: #666; font-size: 13px;">
            This email was sent because our AI onboarding agent was unable to reach you by phone.
            The CS Manager has been CC'd for visibility.
        </p>
    </div>

    <div style="background: #eee; padding: 12px 24px; border-radius: 0 0 8px 8px; text-align: center;">
        <p style="color: #888; font-size: 12px; margin: 0;">NovaCRM Onboarding Agent · Automated System</p>
    </div>
</body>
</html>
"""

    _send_email(
        to=ae_email,
        cc=cs_email,
        subject=subject,
        body_plain=body_plain,
        body_html=body_html,
    )


def notify_ae_malformed_email(ae_email: str, missing_fields: list):
    """Notify the AE their deal email was missing required fields."""
    subject = "[NovaCRM] Deal notification incomplete — please resend"
    body = f"""Hi,

Your deal notification email was received but could not be processed because the following required fields were missing:

Missing: {', '.join(missing_fields)}

Please resend with:
- Customer company name
- Customer contact email
- Your name and email
- Salesforce opportunity link (optional)

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


def notify_cs_escalation(customer_name: str, ae_name: str, ae_email: str, reason: str):
    """Fallback plain-text CS notification for non-email-button escalations."""
    cs_email = os.getenv("CS_MANAGER_EMAIL")
    if not cs_email:
        return

    subject = f"[NovaCRM] Escalation: {customer_name} onboarding blocked"
    body = f"""Hi CS,

The automated onboarding pipeline is blocked for {customer_name}.

AE: {ae_name} ({ae_email})
Reason: {reason}

Please follow up manually.

— NovaCRM Onboarding Agent
"""
    try:
        _send_email(cs_email, subject, body)
    except Exception as e:
        print(f"   ⚠️  Failed to send CS escalation email: {e}")


def notify_task_verification_needed(
    task_name: str,
    project_name: str,
    customer_name: str,
    phase_name: str,
    recipient_email: str,
    confirmation_token: str,
    webhook_base_url: str,
):
    """Send verification email when a critical task is marked complete.

    Used for the 'Data verification sign-off' task — the last task in
    Data Migration — since the brief explicitly calls out cases where
    data migration was marked done without real verification.

    The same pattern applies to any phase sign-off task.
    """
    yes_url = f"{webhook_base_url}/confirm-task?token={confirmation_token}&action=yes"
    no_url = f"{webhook_base_url}/confirm-task?token={confirmation_token}&action=no"

    subject = f"[NovaCRM] Verification needed: {task_name} — {project_name}"

    body_plain = f"""Hi,

The following task has been marked as complete in Rocketlane and requires your verification:

Project: {project_name}
Customer: {customer_name}
Phase: {phase_name}
Task: {task_name}

Please confirm whether this task has been genuinely completed:

YES — Task is verified and complete:
{yes_url}

NO — Task needs to be reopened:
{no_url}

This verification step exists to prevent tasks being marked done without actual completion,
particularly for data migration which directly impacts customer go-live.

— NovaCRM Onboarding Agent
"""

    body_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: #1a1a2e; padding: 20px; border-radius: 8px 8px 0 0;">
        <h2 style="color: white; margin: 0;">NovaCRM Onboarding</h2>
        <p style="color: #aaa; margin: 5px 0 0 0;">Task Verification Required</p>
    </div>

    <div style="background: #f9f9f9; padding: 24px; border: 1px solid #eee;">
        <p>The following task has been marked as <strong>complete</strong> and requires your verification:</p>

        <div style="background: white; border: 1px solid #ddd; border-radius: 6px; padding: 16px; margin: 16px 0;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 6px 0; color: #666; width: 35%;">Project</td>
                    <td style="padding: 6px 0;"><strong>{project_name}</strong></td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #666;">Customer</td>
                    <td style="padding: 6px 0;">{customer_name}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #666;">Phase</td>
                    <td style="padding: 6px 0;">{phase_name}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #666;">Task</td>
                    <td style="padding: 6px 0;"><strong>{task_name}</strong></td>
                </tr>
            </table>
        </div>

        <p>Has this task been <strong>genuinely completed and verified</strong>?</p>

        <div style="text-align: center; margin: 32px 0;">
            <a href="{yes_url}"
               style="background: #16a34a; color: white; padding: 14px 28px; border-radius: 6px;
                      text-decoration: none; font-weight: bold; margin: 0 8px; display: inline-block;">
                ✅ Yes, verified & complete
            </a>
            <a href="{no_url}"
               style="background: #dc2626; color: white; padding: 14px 28px; border-radius: 6px;
                      text-decoration: none; font-weight: bold; margin: 0 8px; display: inline-block;">
                ❌ No, reopen task
            </a>
        </div>

        <div style="background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; padding: 12px; margin-top: 16px;">
            <p style="margin: 0; color: #92400e; font-size: 13px;">
                ⚠️ <strong>Why this matters:</strong> Data migration tasks marked done without
                verification have caused escalations in the past. This step ensures
                {customer_name}'s data is genuinely ready before go-live.
            </p>
        </div>
    </div>

    <div style="background: #eee; padding: 12px 24px; border-radius: 0 0 8px 8px; text-align: center;">
        <p style="color: #888; font-size: 12px; margin: 0;">NovaCRM Onboarding Agent · Automated Verification System</p>
    </div>
</body>
</html>
"""
    _send_email(
        to=recipient_email,
        subject=subject,
        body_plain=body_plain,
        body_html=body_html,
    )