"""Shared structured logging.

Same pattern used across every agent (intake, voice, comms) so the whole
pipeline produces one consistent audit trail for Part 3's guardrail
requirement: timestamp, what happened, and why.
"""

import json
import os
from datetime import datetime, timezone

LOG_PATH = os.getenv(
    "AGENT_LOG_PATH", os.path.join(os.path.dirname(__file__), "agent_log.jsonl")
)


def log_event(event: dict):
    """Append a structured, timestamped event to the shared log file."""
    event = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")
    print(f"LOGGED: {event}")