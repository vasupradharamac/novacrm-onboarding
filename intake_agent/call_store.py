"""Shared call store — persists pending call metadata to a JSON file.

Because the poller and webhook receiver run as separate processes,
an in-memory dict can't be shared between them. This file-based store
lets both processes read and write the same call_id → deal metadata mapping.
"""

import json
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "pending_calls.json")


def save_pending_call(call_id: str, deal_metadata: dict):
    store = _load()
    store[call_id] = deal_metadata
    _save(store)


def get_pending_call(call_id: str) -> dict:
    return _load().get(call_id, {})


def delete_pending_call(call_id: str):
    store = _load()
    store.pop(call_id, None)
    _save(store)


def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH) as f:
        return json.load(f)


def _save(store: dict):
    with open(STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)