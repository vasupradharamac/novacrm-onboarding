"""Shared call store — SQLite backed.

Stores pending call metadata, retry count, and confirmation token
so both the poller and webhook_receiver can share state across processes.
"""

import json
import os
import secrets
import sqlite3
from dotenv import load_dotenv

load_dotenv()


def _get_db_path() -> str:
    return os.getenv(
        "CALL_STORE_PATH",
        os.path.join(os.path.expanduser("~"), "novacrm_call_store.db")
    )


def _get_conn():
    conn = sqlite3.connect(_get_db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_calls (
            call_id TEXT PRIMARY KEY,
            metadata TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            confirmation_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rocketlane_retry_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT,
            plan_tier TEXT,
            deal_metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def save_pending_call(call_id: str, deal_metadata: dict):
    token = secrets.token_urlsafe(16)
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO pending_calls
           (call_id, metadata, retry_count, confirmation_token)
           VALUES (?, ?, 0, ?)""",
        (call_id, json.dumps(deal_metadata), token)
    )
    conn.commit()
    conn.close()
    print(f"✅ call_store: saved {call_id} → {deal_metadata.get('customer_name')} [{_get_db_path()}]")
    return token


def get_pending_call(call_id: str) -> dict:
    print(f"DEBUG call_store: reading {call_id} from {_get_db_path()}")
    conn = _get_conn()
    row = conn.execute(
        "SELECT metadata, retry_count, confirmation_token FROM pending_calls WHERE call_id = ?",
        (call_id,)
    ).fetchone()
    conn.close()
    if row:
        data = json.loads(row[0])
        data["_retry_count"] = row[1]
        data["_confirmation_token"] = row[2]
        print(f"DEBUG call_store: found {data.get('customer_name')} (retry #{row[1]})")
        return data
    print(f"DEBUG call_store: NOT FOUND for {call_id}")
    return {}


def increment_retry(call_id: str, new_call_id: str):
    """Register a retry call — saves new_call_id with retry_count+1, removes old."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT metadata, retry_count, confirmation_token FROM pending_calls WHERE call_id = ?",
        (call_id,)
    ).fetchone()
    if row:
        new_retry_count = row[1] + 1
        conn.execute(
            """INSERT OR REPLACE INTO pending_calls
               (call_id, metadata, retry_count, confirmation_token)
               VALUES (?, ?, ?, ?)""",
            (new_call_id, row[0], new_retry_count, row[2])
        )
        conn.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))
        conn.commit()
        print(f"✅ call_store: retry #{new_retry_count} registered as {new_call_id}")
    else:
        print(f"⚠️  call_store: increment_retry — original call_id {call_id} not found")
    conn.close()


def get_by_token(token: str) -> tuple:
    """Look up a pending call by its confirmation token."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT call_id, metadata FROM pending_calls WHERE confirmation_token = ?",
        (token,)
    ).fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1])
    return None, {}


def delete_pending_call(call_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))
    conn.commit()
    conn.close()


def queue_rocketlane_retry(customer_name: str, plan_tier: str, deal: dict):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO rocketlane_retry_queue (customer_name, plan_tier, deal_metadata) VALUES (?, ?, ?)",
        (customer_name, plan_tier, json.dumps(deal))
    )
    conn.commit()
    conn.close()
    print(f"   📋 Queued Rocketlane retry for {customer_name} ({plan_tier})")


def get_rocketlane_retry_queue() -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, customer_name, plan_tier, deal_metadata FROM rocketlane_retry_queue"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "customer_name": r[1], "plan_tier": r[2], "deal": json.loads(r[3])} for r in rows]


def clear_rocketlane_retry(retry_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM rocketlane_retry_queue WHERE id = ?", (retry_id,))
    conn.commit()
    conn.close()


def list_pending_calls() -> dict:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT call_id, metadata, retry_count FROM pending_calls"
    ).fetchall()
    conn.close()
    return {row[0]: {**json.loads(row[1]), "_retry_count": row[2]} for row in rows}


if __name__ == "__main__":
    import sys
    if "--seed" in sys.argv:
        save_pending_call("call_test_enterprise_001", {
            "customer_name": "Google LLC",
            "customer_contact_email": "sarah.chen@google.com",
            "ae_name": "Jordan Lee",
            "ae_email": "vasupradha.1011@gmail.com",
            "salesforce_link": "https://novacrm.lightning.force.com/lightning/r/Opportunity/0061234567GOOGLE/view"
        })
        save_pending_call("call_test_growth_001", {
            "customer_name": "Stripe Inc",
            "customer_contact_email": "john.doe@stripe.com",
            "ae_name": "Jordan Lee",
            "ae_email": "vasupradha.1011@gmail.com",
            "salesforce_link": "https://novacrm.lightning.force.com/lightning/r/Opportunity/0061234567STRIPE/view"
        })
        print("✅ Test data seeded")
    else:
        print(f"DB path: {_get_db_path()}")
        print(f"Pending calls: {list_pending_calls()}")