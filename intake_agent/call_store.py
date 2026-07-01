"""Shared call store — SQLite backed.

DB path is resolved fresh on every call (not at import time) so it
always picks up the correct CALL_STORE_PATH env var regardless of
when load_dotenv() runs relative to this module being imported.
"""

import json
import os
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def save_pending_call(call_id: str, deal_metadata: dict):
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_calls (call_id, metadata) VALUES (?, ?)",
            (call_id, json.dumps(deal_metadata))
        )
    print(f"✅ call_store: saved {call_id} → {deal_metadata.get('customer_name')} [{_get_db_path()}]")


def get_pending_call(call_id: str) -> dict:
    path = _get_db_path()
    print(f"DEBUG call_store: reading {call_id} from {path}")
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT metadata FROM pending_calls WHERE call_id = ?",
            (call_id,)
        ).fetchone()
    if row:
        data = json.loads(row[0])
        print(f"DEBUG call_store: found {data.get('customer_name')}")
        return data
    print(f"DEBUG call_store: NOT FOUND for {call_id}")
    return {}


def delete_pending_call(call_id: str):
    with _get_conn() as conn:
        conn.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))


def list_pending_calls() -> dict:
    with _get_conn() as conn:
        rows = conn.execute("SELECT call_id, metadata FROM pending_calls").fetchall()
    return {row[0]: json.loads(row[1]) for row in rows}


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