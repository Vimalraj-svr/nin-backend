import json
import logging
import threading
import time
from datetime import timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_KEYS_PATH = Path(__file__).parent.parent.parent / "firebase" / "keys.json"

# ── Firestore client ───────────────────────────────────────────────────────────

try:
    from google.cloud import firestore as _fs
    from google.oauth2 import service_account as _sa
    _FS_PKG = True
except ImportError as _e:
    logger.warning("google-cloud-firestore not installed — Firestore disabled: %s", _e)
    _FS_PKG = False

_db_client = None
_db_lock = threading.Lock()


def _db():
    global _db_client
    if not _FS_PKG:
        return None
    if _db_client is not None:
        return _db_client
    with _db_lock:
        if _db_client is not None:
            return _db_client
        if not _KEYS_PATH.exists():
            logger.warning("Firebase keys not found at %s — Firestore disabled", _KEYS_PATH)
            return None
        try:
            creds = _sa.Credentials.from_service_account_file(
                str(_KEYS_PATH),
                scopes=["https://www.googleapis.com/auth/datastore"],
            )
            with open(_KEYS_PATH) as f:
                project = json.load(f).get("project_id", "ninaivugal-73074")
            _db_client = _fs.Client(credentials=creds, project=project)
        except Exception as e:
            logger.warning("Firestore init failed: %s — Firestore disabled", e)
            return None
    return _db_client


def _run_async(fn):
    threading.Thread(target=fn, daemon=True).start()


# ── Notification writes ────────────────────────────────────────────────────────

def write_notification(user_id: str, notif: dict) -> None:
    def _write():
        db = _db()
        if db is None:
            return
        try:
            notif_id = notif.get("id") or str(notif.get("_id", user_id))
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
    def _mark():
        db = _db()
        if db is None:
            return
        try:
            items_ref = db.collection("notifications").document(user_id).collection("items")
            unread = items_ref.where("read", "==", False).stream()
            batch = db.batch()
            count = 0
            for doc in unread:
                batch.update(doc.reference, {"read": True})
                count += 1
                if count == 500:
                    batch.commit()
                    batch = db.batch()
                    count = 0
            if count:
                batch.commit()
        except Exception as e:
            logger.warning("Firestore mark_all_read failed: %s", e)

    _run_async(_mark)


# ── Firebase custom token (for frontend Firestore auth) ───────────────────────

def create_custom_token(user_id: str) -> str:
    """
    Create a Firebase custom token signed with the service account private key.
    Uses PyJWT directly — no firebase-admin needed.
    """
    if not _KEYS_PATH.exists():
        raise RuntimeError("Firebase keys not found — cannot issue custom token")
    try:
        import jwt  # PyJWT, already in requirements
        with open(_KEYS_PATH) as f:
            sa = json.load(f)
        now = int(time.time())
        payload = {
            "iss": sa["client_email"],
            "sub": sa["client_email"],
            "aud": "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
            "iat": now,
            "exp": now + 3600,
            "uid": user_id,
        }
        token = jwt.encode(payload, sa["private_key"], algorithm="RS256")
        return token if isinstance(token, str) else token.decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Could not create custom token: {e}") from e
