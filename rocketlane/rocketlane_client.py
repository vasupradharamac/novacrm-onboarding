"""Rocketlane API client.

Creates onboarding projects from the correct template based on plan tier.
Enterprise → 30-day template with dedicated CSM
Growth → 14-day template with pooled CSM

API ref: https://developer.rocketlane.com/reference/create-project
"""

import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

ROCKETLANE_BASE_URL = "https://api.rocketlane.com/api/1.0"


class RocketlaneError(Exception):
    pass


class DuplicateProjectError(RocketlaneError):
    pass


class RocketlaneAPIDownError(RocketlaneError):
    pass


def _get_headers():
    return {
        "api-key": os.getenv("ROCKETLANE_API_KEY"),
        "Content-Type": "application/json",
    }


def project_exists(customer_name: str) -> bool:
    """Check if an onboarding project already exists for this customer.

    Guards against the edge case where a duplicate deal email triggers
    a second project creation for the same customer.
    """
    try:
        response = httpx.get(
            f"{ROCKETLANE_BASE_URL}/projects",
            headers=_get_headers(),
            params={"search": customer_name},
            timeout=10,
        )
        if response.status_code == 200:
            projects = response.json().get("data", [])
            for project in projects:
                if customer_name.lower() in project.get("projectName", "").lower():
                    return True
        return False
    except httpx.RequestError:
        return False


def create_onboarding_project(
    customer_name: str,
    plan_tier: str,
    customer_contact_email: str,
    ae_name: str,
) -> dict:
    """Create a Rocketlane onboarding project from the correct template.

    Returns the created project data on success.
    Raises DuplicateProjectError if a project already exists.
    Raises RocketlaneAPIDownError if the API is unreachable.
    Raises RocketlaneError for other failures.
    """
    if not os.getenv("ROCKETLANE_API_KEY"):
        raise RocketlaneError("ROCKETLANE_API_KEY not set")

    # Pick the right template + config based on tier
    if plan_tier == "enterprise":
        template_id = os.getenv("ROCKETLANE_ENTERPRISE_TEMPLATE_ID")
        onboarding_days = 30
        csm_type = "dedicated CSM"
    else:
        template_id = os.getenv("ROCKETLANE_GROWTH_TEMPLATE_ID")
        onboarding_days = 14
        csm_type = "pooled CSM"

    if not template_id:
        raise RocketlaneError(
            f"Template ID not set for {plan_tier} tier — "
            f"set ROCKETLANE_{plan_tier.upper()}_TEMPLATE_ID in .env"
        )

    # Guard against duplicates
    if project_exists(customer_name):
        raise DuplicateProjectError(
            f"An onboarding project for '{customer_name}' already exists. "
            "Skipping to avoid duplicate."
        )

    start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "projectName": f"{customer_name} Onboarding",
        "customer": {"companyName": customer_name},
        "autoCreateCompany": True,
        "owner": {"emailId": os.getenv("ROCKETLANE_OWNER_EMAIL")},
        "startDate": start_date,
        "sources": [
            {
                "startDate": start_date,
                "templateId": int(template_id),
            }
        ],
    }

    try:
        response = httpx.post(
            f"{ROCKETLANE_BASE_URL}/projects",
            headers=_get_headers(),
            json=payload,
            timeout=15,
        )
    except httpx.TimeoutException:
        raise RocketlaneAPIDownError("Rocketlane API timed out")
    except httpx.RequestError as e:
        raise RocketlaneAPIDownError(f"Rocketlane API unreachable: {e}")

    if response.status_code in (200, 201):
        data = response.json()
        project_id = data.get("projectId", "unknown")
        print(f"\n✅ Rocketlane project created!")
        print(f"   Customer: {customer_name}")
        print(f"   Plan: {plan_tier.title()} ({onboarding_days}-day, {csm_type})")
        print(f"   Project ID: {project_id}")
        return data
    elif response.status_code == 503:
        raise RocketlaneAPIDownError(f"Rocketlane API is down ({response.status_code})")
    else:
        raise RocketlaneError(
            f"Rocketlane API returned {response.status_code}: {response.text}"
        )


def update_task_status(task_id: int, completed: bool) -> dict:
    """Update a task's status to completed or reopen it."""
    status_value = 3 if completed else 2
    status_label = "Completed" if completed else "In Progress"

    try:
        response = httpx.put(
            f"{ROCKETLANE_BASE_URL}/tasks/{task_id}",
            headers=_get_headers(),
            json={"status": {"value": status_value}},
            timeout=30,
        )
        if response.status_code in (200, 201):
            print(f"   ✅ Task {task_id} → {status_label}")
            return response.json()
        else:
            raise RocketlaneError(
                f"Task update failed: {response.status_code} {response.text}"
            )
    except httpx.TimeoutException:
        raise RocketlaneAPIDownError("Rocketlane API timed out on task update")
    except httpx.RequestError as e:
        raise RocketlaneAPIDownError(f"Network error: {e}")