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
    conn.commit()
    return conn


def save_pending_call(call_id: str, deal_metadata: dict):
    token = secrets.token_urlsafe(16)
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO pending_calls
               (call_id, metadata, retry_count, confirmation_token)
               VALUES (?, ?, 0, ?)""",
            (call_id, json.dumps(deal_metadata), token)
        )
    print(f"✅ call_store: saved {call_id} → {deal_metadata.get('customer_name')} [{_get_db_path()}]")
    return token


def get_pending_call(call_id: str) -> dict:
    path = _get_db_path()
    print(f"DEBUG call_store: reading {call_id} from {path}")
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT metadata, retry_count, confirmation_token FROM pending_calls WHERE call_id = ?",
            (call_id,)
        ).fetchone()
    if row:
        data = json.loads(row[0])
        data["_retry_count"] = row[1]
        data["_confirmation_token"] = row[2]
        print(f"DEBUG call_store: found {data.get('customer_name')} (retry #{row[1]})")
        return data
    print(f"DEBUG call_store: NOT FOUND for {call_id}")
    return {}


def increment_retry(call_id: str, new_call_id: str):
    """Mark the original call as retried and register the new call_id."""
    with _get_conn() as conn:
        # Get original metadata
        row = conn.execute(
            "SELECT metadata, retry_count, confirmation_token FROM pending_calls WHERE call_id = ?",
            (call_id,)
        ).fetchone()
        if row:
            # Save under new call_id with incremented retry count
            conn.execute(
                """INSERT OR REPLACE INTO pending_calls
                   (call_id, metadata, retry_count, confirmation_token)
                   VALUES (?, ?, ?, ?)""",
                (new_call_id, row[0], row[1] + 1, row[2])
            )
            # Remove old call_id
            conn.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))


def get_by_token(token: str) -> tuple:
    """Look up a pending call by its confirmation token. Returns (call_id, metadata)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT call_id, metadata FROM pending_calls WHERE confirmation_token = ?",
            (token,)
        ).fetchone()
    if row:
        return row[0], json.loads(row[1])
    return None, {}


def delete_pending_call(call_id: str):
    with _get_conn() as conn:
        conn.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))


def list_pending_calls() -> dict:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT call_id, metadata, retry_count FROM pending_calls"
        ).fetchall()
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