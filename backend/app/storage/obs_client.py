"""S3-compatible client for the optional OBS (object storage) upload
mirror -- boto3's S3 client talks to any S3-compatible endpoint (OBS,
MinIO, Ceph, ...) via a custom endpoint_url, so no vendor-specific SDK is
needed. See storage/obs_settings.py for where credentials/the enabled
flag live, and why the secret key never leaves this process decrypted.
"""
import asyncio
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings
from app.storage.obs_settings import (
    OBSCredentialFull,
    get_active_credential_full,
    get_credential_full,
    is_enabled,
    mark_verified,
)

logger = logging.getLogger(__name__)


def _normalize_endpoint(endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"https://{endpoint}"


def _client_for(credential: OBSCredentialFull):
    settings = get_settings()
    boto_config = Config(
        connect_timeout=settings.obs_connect_timeout_s,
        read_timeout=settings.obs_read_timeout_s,
        # A single attempt, not boto3's default retry-with-backoff --
        # this call sits in the critical path of an upload request (or a
        # user-initiated "test connection" click); a hung/unreachable
        # bucket should fail fast and visibly, not silently retry for
        # much longer than the configured timeouts already suggest.
        retries={"max_attempts": 1},
    )
    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=_normalize_endpoint(credential.endpoint),
        aws_access_key_id=credential.access_key,
        aws_secret_access_key=credential.secret_key,
        # boto3 requires SOME region even against a fully custom
        # S3-compatible endpoint that doesn't use AWS regions at all --
        # falls back to a harmless placeholder when the credential didn't
        # specify one.
        region_name=credential.region or "us-east-1",
        config=boto_config,
    )


def _object_key(credential: OBSCredentialFull, job_id: str, filename: str) -> str:
    prefix = credential.path_prefix.strip("/")
    parts = [p for p in (prefix, job_id, filename) if p]
    return "/".join(parts)


def _check_bucket(credential: OBSCredentialFull) -> tuple[bool, str]:
    client = _client_for(credential)
    try:
        client.head_bucket(Bucket=credential.bucket)
        return True, f'Reachable — bucket "{credential.bucket}" is accessible with this credential.'
    except ClientError as exc:
        error = exc.response.get("Error", {})
        return False, f"Bucket check failed ({error.get('Code', 'Unknown')}): {error.get('Message', str(exc))}"
    except BotoCoreError as exc:
        return False, f"Could not reach endpoint: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Unexpected error testing connection: {exc}"


async def test_credential(credential_id: str) -> tuple[bool, str]:
    """Runs a real HeadBucket call -- the standard S3-compatible way to
    check both reachability and read permission without listing or
    touching any objects. Offloaded to a thread (boto3 is synchronous) so
    it never blocks the event loop; the Config in _client_for bounds how
    long an unreachable endpoint can hang this for. Records the result
    via mark_verified so it shows up next to the credential in the
    Settings UI without the frontend needing a second round-trip.
    """
    credential = get_credential_full(credential_id)
    if credential is None:
        return False, "Credential not found."
    ok, detail = await asyncio.to_thread(_check_bucket, credential)
    mark_verified(credential_id, ok, detail)
    return ok, detail


async def mirror_upload(job_id: str, filename: str, content: bytes) -> None:
    """Best-effort: pushes a copy of an uploaded file to the active OBS
    credential's bucket, if OBS uploads are enabled and a credential is
    marked active. Never raises -- callers (routes_ingest.py) treat this
    exactly like this app's other optional enrichment steps (translation,
    the dataset library mirror): log and move on, never block or fail the
    upload the user is actually waiting on because of a bucket problem.
    """
    if not is_enabled():
        return
    credential = get_active_credential_full()
    if credential is None:
        return

    def _put() -> None:
        client = _client_for(credential)
        key = _object_key(credential, job_id, filename)
        client.put_object(Bucket=credential.bucket, Key=key, Body=content)

    try:
        await asyncio.to_thread(_put)
    except Exception:
        logger.exception(
            "OBS mirror upload failed for %s (job %s) -- file was still saved to local disk normally.",
            filename, job_id,
        )
