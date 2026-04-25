import os
import base64
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_raw = os.getenv("ENCRYPTION_KEY", "")
if _raw:
    _key = _raw.encode()
else:
    _key = Fernet.generate_key()
    logger.warning("ENCRYPTION_KEY not set — generated ephemeral key. Set ENCRYPTION_KEY in .env to persist data across restarts.")

_fernet = Fernet(_key)


def encrypt(text: str) -> str:
    return _fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


def encrypt_if_present(value) -> str | None:
    if value is None:
        return None
    return encrypt(str(value))


def decrypt_if_present(value) -> str | None:
    if value is None:
        return None
    try:
        return decrypt(str(value))
    except (InvalidToken, Exception):
        # Value may be unencrypted legacy data — return as-is
        return str(value)
