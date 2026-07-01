"""Communication Agent — Agent 2.

Creates a personalized Slack channel for the customer's onboarding,
sets the channel topic, and posts a welcome message tailored to their
plan tier. Enterprise and Growth customers get meaningfully different
experiences — different tone, different CSM assignment, different timeline.
"""

import os
import re

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "intake_agent"))
from agent_logger import log_event


def _get_client() -> WebClient:
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN not set")
    return WebClient(token=token)


def _sanitize_channel_name(name: str) -> str:
    """Slack channel names: lowercase, no spaces, only letters/numbers/hyphens, max 80 chars."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name[:80]


def _build_channel_name(customer_name: str, plan_tier: str) -> str:
    """Channel naming convention:
    Enterprise: onboarding-google-llc-enterprise
    Growth:     onboarding-google-llc-growth
    """
    clean = _sanitize_channel_name(customer_name)
    return f"onboarding-{clean}-{plan_tier}"


def _build_topic(customer_name: str, plan_tier: str) -> str:
    if plan_tier == "enterprise":
        return (
            f"🚀 {customer_name} Enterprise Onboarding | "
            "30-day program | Dedicated CSM | "
            "Phases: Kickoff → Data Migration → Configuration → Go-Live"
        )
    else:
        return (
            f"🚀 {customer_name} Growth Onboarding | "
            "14-day program | Pooled CSM | "
            "Phases: Kickoff → Data Migration → Configuration → Go-Live"
        )


def _build_welcome_message(customer_name: str, plan_tier: str) -> str:
    if plan_tier == "enterprise":
        return (
            f":wave: *Welcome to NovaCRM, {customer_name}!*\n\n"
            f"We're thrilled to have you on board as an *Enterprise* customer. "
            f"This channel is your dedicated space for onboarding communication.\n\n"
            f":white_check_mark: *Your onboarding at a glance:*\n"
            f"• *Program duration:* 30 days\n"
            f"• *CSM:* You have a dedicated Customer Success Manager assigned to you\n"
            f"• *Phases:* Kickoff → Data Migration → Configuration → Go-Live\n\n"
            f":calendar: Your dedicated CSM will reach out within 24 hours to schedule your kickoff call.\n\n"
            f"In the meantime, feel free to ask any questions here. "
            f"We're excited to help {customer_name} get the most out of NovaCRM! :rocket:"
        )
    else:
        return (
            f":wave: *Welcome to NovaCRM, {customer_name}!*\n\n"
            f"We're excited to have you on board as a *Growth* customer. "
            f"This channel is your onboarding hub for the next two weeks.\n\n"
            f":white_check_mark: *Your onboarding at a glance:*\n"
            f"• *Program duration:* 14 days\n"
            f"• *CSM:* Our pooled Customer Success team is here to support you\n"
            f"• *Phases:* Kickoff → Data Migration → Configuration → Go-Live\n\n"
            f":calendar: Our team will reach out shortly to schedule your kickoff call.\n\n"
            f"Drop any questions here anytime — "
            f"we're looking forward to a smooth onboarding for {customer_name}! :rocket:"
        )


def create_onboarding_channel(
    customer_name: str,
    plan_tier: str,
    customer_contact_email: str,
) -> dict:
    """Create a Slack onboarding channel for the customer.

    Returns a dict with channel_id, channel_name, and channel_url.
    Raises SlackApiError on failure.
    """
    client = _get_client()
    channel_name = _build_channel_name(customer_name, plan_tier)
    topic = _build_topic(customer_name, plan_tier)
    welcome = _build_welcome_message(customer_name, plan_tier)

    log_event({
        "event": "slack_channel_creating",
        "customer": customer_name,
        "plan_tier": plan_tier,
        "channel_name": channel_name,
    })

    # Step 1: create the channel
    try:
        create_response = client.conversations_create(name=channel_name)
        channel_id = create_response["channel"]["id"]
        print(f"\n💬 Slack channel created: #{channel_name} ({channel_id})")
    except SlackApiError as e:
        if e.response["error"] == "name_taken":
            log_event({
                "event": "slack_channel_already_exists",
                "channel_name": channel_name,
                "customer": customer_name,
            })
            print(f"   ⚠️  Channel #{channel_name} already exists — skipping creation")
            # Find the existing channel
            list_response = client.conversations_list(types="public_channel")
            existing = next(
                (c for c in list_response["channels"] if c["name"] == channel_name),
                None
            )
            if not existing:
                raise
            channel_id = existing["id"]
        else:
            raise

    # Step 2: set the topic
    try:
        client.conversations_setTopic(channel=channel_id, topic=topic)
        print(f"   ✅ Topic set")
    except SlackApiError as e:
        print(f"   ⚠️  Could not set topic: {e.response['error']}")

    # Step 3: post the welcome message
    try:
        client.chat_postMessage(channel=channel_id, text=welcome)
        print(f"   ✅ Welcome message posted")
    except SlackApiError as e:
        print(f"   ⚠️  Could not post welcome message: {e.response['error']}")

    result = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_url": f"https://novacrmonboar-or55597.slack.com/channels/{channel_name}",
        "plan_tier": plan_tier,
        "customer": customer_name,
    }

    log_event({
        "event": "slack_channel_created",
        "customer": customer_name,
        "plan_tier": plan_tier,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "decision_rationale": (
            f"Created #{channel_name} for {customer_name} "
            f"({plan_tier} tier — {'30-day dedicated CSM' if plan_tier == 'enterprise' else '14-day pooled CSM'}). "
            "Channel name, topic, and welcome message all personalized to plan tier."
        ),
    })

    return result