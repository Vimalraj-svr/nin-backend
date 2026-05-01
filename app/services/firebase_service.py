import logging
import threading
from pathlib import Path
from datetime import timezone

import firebase_admin
from firebase_admin import credentials, firestore, auth as fb_auth
from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger(__name__)

_app: firebase_admin.App | None = None
_init_lock = threading.Lock()
_KEYS_PATH = Path(__file__).parent.parent.parent / "firebase" / "keys.json"


def _get_app() -> firebase_admin.App | None:
    global _app
    if _app is not None:
        return _app
    with _init_lock:
        if _app is not None:
            return _app
        if not _KEYS_PATH.exists():
            logger.warning("Firebase keys not found at %s — Firestore disabled", _KEYS_PATH)
            return None
        try:
            cred = credentials.Certificate(str(_KEYS_PATH))
            _app = firebase_admin.initialize_app(cred)
        except Exception as e:
            logger.warning("Firebase init failed: %s — Firestore disabled", e)
            return None
    return _app


def _db():
    app = _get_app()
    if app is None:
        return None
    try:
        return firestore.client()
    except Exception as e:
        logger.warning("Firestore client() failed: %s", e)
        return None


def _run_async(fn):
    """Fire-and-forget in a daemon thread so it never blocks a request."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()


def write_notification(user_id: str, notif: dict) -> None:
    """
    Write a notification into Firestore:
      notifications/{user_id}/items/{notif_id}
    Runs in a background thread — never blocks the request.
    """
    def _write():
        db = _db()
        if db is None:
            return
        try:
            notif_id = notif.get("id") or str(notif.get("_id", user_id))
            # Firestore needs a serialisable timestamp
            created = notif.get("created_at")
            if hasattr(created, "isoformat"):
                created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
            db.collection("notifications").document(user_id) \
              .collection("items").document(notif_id).set({
                  "id": notif_id,
                  "type": notif.get("type"),
                  "actor_id": notif.get("actor_id", ""),
                  "actor_name": notif.get("actor_name", ""),
                  "entry_id": notif.get("entry_id"),
                  "read": False,
                  "created_at": created,
              })
        except Exception as e:
            logger.warning("Firestore write_notification failed: %s", e)

    _run_async(_write)


def mark_all_read_firestore(user_id: str) -> None:
    """Mark all unread items read in Firestore. Runs in a background thread."""
    def _mark():
        db = _db()
        if db is None:
            return
        try:
            items_ref = db.collection("notifications").document(user_id).collection("items")
            unread = items_ref.where(filter=FieldFilter("read", "==", False)).stream()
            batch = db.batch()
            count = 0
            for doc in unread:
                batch.update(doc.reference, {"read": True})
                count += 1
                if count == 500:  # Firestore batch limit
                    batch.commit()
                    batch = db.batch()
                    count = 0
            if count:
                batch.commit()
        except Exception as e:
            logger.warning("Firestore mark_all_read failed: %s", e)

    _run_async(_mark)


def create_custom_token(user_id: str) -> str:
    """Create a Firebase custom token. Raises if Firebase is not configured."""
    app = _get_app()
    if app is None:
        raise RuntimeError("Firebase not configured — cannot issue custom token")
    token_bytes = fb_auth.create_custom_token(user_id)
    return token_bytes.decode("utf-8") if isinstance(token_bytes, bytes) else token_bytes
