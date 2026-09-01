"""Persistent store for OBS (S3-compatible object storage) settings and
credential profiles -- the opt-in "mirror uploaded files to a bucket"
feature, off by default (see config.Settings.obs_settings_db_path and
the air-gapped-by-default framing throughout this app).

A deployment can save several AK/SK credential profiles (different
buckets/regions/accounts) and mark exactly one "active" -- the one
storage/obs_client.py actually uses when mirroring an upload. The secret
key is Fernet-encrypted at rest and NEVER returned to the frontend after
it's first saved; only a short masked hint (see _mask_secret) is, which
is why OBSCredentialPublic (models/schemas.py) has no secret_key field at
all -- there's nothing to decrypt it back from on that side, by design.
"""
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.models.schemas import OBSCredentialPublic

logger = logging.getLogger(__name__)

_CONFIG_DDL = '''
CREATE TABLE IF NOT EXISTS "_obs_config" (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0
)
'''

_CREDENTIALS_DDL = '''
CREATE TABLE IF NOT EXISTS "_obs_credentials" (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    region TEXT,
    bucket TEXT NOT NULL,
    path_prefix TEXT NOT NULL DEFAULT '',
    access_key TEXT NOT NULL,
    secret_key_encrypted BLOB NOT NULL,
    secret_key_hint TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    verified_ok INTEGER,
    verified_detail TEXT
)
'''


@dataclass
class OBSCredentialFull:
    """Internal-only shape carrying the DECRYPTED secret key -- used by
    storage/obs_client.py to actually connect. Never constructed outside
    this module, never serialized back through the API (compare
    OBSCredentialPublic, which is what routes_settings.py returns)."""
    id: str
    name: str
    endpoint: str
    region: str | None
    bucket: str
    path_prefix: str
    access_key: str
    secret_key: str


def db_path() -> Path:
    path = Path(get_settings().obs_settings_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()))
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(_CONFIG_DDL)
    conn.execute(_CREDENTIALS_DDL)
    return conn


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazily builds the Fernet cipher used to encrypt/decrypt secret keys.

    settings.obs_encryption_key lets a deployment supply its own key (e.g.
    injected from an external secrets manager) -- otherwise a key is
    generated once and persisted to obs_secret_key_path so the same key
    is used across restarts (a new random key on every restart would make
    every previously-saved secret undecryptable). File permissions are
    tightened to owner-read/write-only right after writing it, same
    posture as an SSH private key.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    settings = get_settings()
    if settings.obs_encryption_key:
        key = settings.obs_encryption_key.encode("utf-8")
    else:
        key_path = Path(settings.obs_secret_key_path)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                logger.warning("Could not restrict permissions on %s -- the OBS encryption key file "
                                "is readable beyond its owner on this filesystem.", key_path)

    _fernet = Fernet(key)
    return _fernet


def _mask_secret(secret: str) -> str:
    """Never shows more than the first 4 characters -- enough for a human
    to recognize "yes, that's the key I saved" without it being useful to
    anyone reading it off a screen."""
    visible = secret[:4] if len(secret) > 4 else ""
    return f"{visible}{'•' * 12}"


def _row_to_public(row: sqlite3.Row) -> OBSCredentialPublic:
    return OBSCredentialPublic(
        id=row["id"], name=row["name"], endpoint=row["endpoint"], region=row["region"],
        bucket=row["bucket"], path_prefix=row["path_prefix"], access_key=row["access_key"],
        secret_key_hint=row["secret_key_hint"], is_active=bool(row["is_active"]),
        created_at=row["created_at"], verified_at=row["verified_at"],
        verified_ok=bool(row["verified_ok"]) if row["verified_ok"] is not None else None,
        verified_detail=row["verified_detail"],
    )


def is_enabled() -> bool:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT enabled FROM "_obs_config" WHERE id = 1').fetchone()
        return bool(row["enabled"]) if row else False
    finally:
        conn.close()


def set_enabled(enabled: bool) -> None:
    conn = _connect()
    try:
        conn.execute(
            'INSERT INTO "_obs_config" (id, enabled) VALUES (1, ?) '
            "ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled",
            (int(enabled),),
        )
        conn.commit()
    finally:
        conn.close()


def list_credentials() -> list[OBSCredentialPublic]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM "_obs_credentials" ORDER BY created_at ASC').fetchall()
        return [_row_to_public(r) for r in rows]
    finally:
        conn.close()


def create_credential(
    name: str, endpoint: str, region: str | None, bucket: str, path_prefix: str,
    access_key: str, secret_key: str,
) -> OBSCredentialPublic:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        existing_count = conn.execute('SELECT COUNT(*) AS n FROM "_obs_credentials"').fetchone()["n"]
        credential_id = uuid.uuid4().hex
        encrypted = _get_fernet().encrypt(secret_key.encode("utf-8"))
        created_at = datetime.now(timezone.utc).isoformat()
        # The very first credential ever saved becomes active automatically
        # -- there's no meaningful "which one is active" choice to make
        # when there's only one, and requiring a separate activation click
        # for the common single-bucket case would just be friction.
        is_active = existing_count == 0
        conn.execute(
            'INSERT INTO "_obs_credentials" '
            "(id, name, endpoint, region, bucket, path_prefix, access_key, secret_key_encrypted, "
            "secret_key_hint, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (credential_id, name, endpoint, region, bucket, path_prefix, access_key, encrypted,
             _mask_secret(secret_key), int(is_active), created_at),
        )
        conn.commit()
        row = conn.execute('SELECT * FROM "_obs_credentials" WHERE id = ?', (credential_id,)).fetchone()
        return _row_to_public(row)
    finally:
        conn.close()


def update_credential(
    credential_id: str, *, name: str | None = None, endpoint: str | None = None,
    region: str | None = None, bucket: str | None = None, path_prefix: str | None = None,
    access_key: str | None = None, secret_key: str | None = None,
) -> OBSCredentialPublic | None:
    """Partial update -- only fields explicitly passed (not None) change.
    Passing a new secret_key re-encrypts and re-hints it; omitting it
    leaves the previously-saved encrypted secret untouched (so editing a
    profile's bucket name doesn't require re-entering its secret key)."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        existing = conn.execute('SELECT * FROM "_obs_credentials" WHERE id = ?', (credential_id,)).fetchone()
        if existing is None:
            return None

        fields = {
            "name": name if name is not None else existing["name"],
            "endpoint": endpoint if endpoint is not None else existing["endpoint"],
            "region": region if region is not None else existing["region"],
            "bucket": bucket if bucket is not None else existing["bucket"],
            "path_prefix": path_prefix if path_prefix is not None else existing["path_prefix"],
            "access_key": access_key if access_key is not None else existing["access_key"],
        }
        if secret_key is not None:
            fields["secret_key_encrypted"] = _get_fernet().encrypt(secret_key.encode("utf-8"))
            fields["secret_key_hint"] = _mask_secret(secret_key)
        else:
            fields["secret_key_encrypted"] = existing["secret_key_encrypted"]
            fields["secret_key_hint"] = existing["secret_key_hint"]

        conn.execute(
            'UPDATE "_obs_credentials" SET name=?, endpoint=?, region=?, bucket=?, path_prefix=?, '
            "access_key=?, secret_key_encrypted=?, secret_key_hint=?, "
            "verified_at=NULL, verified_ok=NULL, verified_detail=NULL WHERE id=?",
            (fields["name"], fields["endpoint"], fields["region"], fields["bucket"], fields["path_prefix"],
             fields["access_key"], fields["secret_key_encrypted"], fields["secret_key_hint"], credential_id),
        )
        conn.commit()
        row = conn.execute('SELECT * FROM "_obs_credentials" WHERE id = ?', (credential_id,)).fetchone()
        return _row_to_public(row)
    finally:
        conn.close()


def delete_credential(credential_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute('DELETE FROM "_obs_credentials" WHERE id = ?', (credential_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_active(credential_id: str) -> bool:
    conn = _connect()
    try:
        exists = conn.execute('SELECT 1 FROM "_obs_credentials" WHERE id = ?', (credential_id,)).fetchone()
        if exists is None:
            return False
        conn.execute('UPDATE "_obs_credentials" SET is_active = 0')
        conn.execute('UPDATE "_obs_credentials" SET is_active = 1 WHERE id = ?', (credential_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def mark_verified(credential_id: str, ok: bool, detail: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            'UPDATE "_obs_credentials" SET verified_at = ?, verified_ok = ?, verified_detail = ? WHERE id = ?',
            (datetime.now(timezone.utc).isoformat(), int(ok), detail, credential_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_credential_full(credential_id: str) -> OBSCredentialFull | None:
    """Decrypted, internal-only -- used by storage/obs_client.py right
    before making the actual S3-compatible call. Returns None both when
    the credential doesn't exist and when its stored secret fails to
    decrypt (a corrupted row, or the encryption key changed underneath
    it) -- callers treat both the same way: "can't connect with this
    credential right now," not a crash."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM "_obs_credentials" WHERE id = ?', (credential_id,)).fetchone()
        if row is None:
            return None
        try:
            secret = _get_fernet().decrypt(bytes(row["secret_key_encrypted"])).decode("utf-8")
        except InvalidToken:
            logger.error("Could not decrypt secret key for OBS credential %s -- encryption key mismatch?", credential_id)
            return None
        return OBSCredentialFull(
            id=row["id"], name=row["name"], endpoint=row["endpoint"], region=row["region"],
            bucket=row["bucket"], path_prefix=row["path_prefix"], access_key=row["access_key"],
            secret_key=secret,
        )
    finally:
        conn.close()


def get_active_credential_full() -> OBSCredentialFull | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT id FROM "_obs_credentials" WHERE is_active = 1').fetchone()
        return get_credential_full(row["id"]) if row else None
    finally:
        conn.close()
