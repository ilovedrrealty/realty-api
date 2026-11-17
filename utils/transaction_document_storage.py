"""Immutable storage for transaction document versions. Authorize in the calling service."""

import hashlib
import io
import re
import warnings
from dataclasses import dataclass
from uuid import UUID

from PIL import Image

from config import settings
from utils.storage import _get_client

MAX_DOCUMENT_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG"}
DOCUMENT_TYPES = {"application/pdf", *IMAGE_FORMATS}


class InvalidDocument(ValueError):
    pass


class DocumentStorageUnavailable(RuntimeError):
    pass


class DocumentIntegrityError(DocumentStorageUnavailable):
    pass


@dataclass(frozen=True)
class DocumentContent:
    content_type: str
    byte_count: int
    sha256: str


def prepare_document(data: bytes, content_type: str) -> DocumentContent:
    """Check type and size while preserving the original document bytes."""
    if content_type not in DOCUMENT_TYPES:
        raise InvalidDocument("Only PDF, JPEG, and PNG documents are supported")
    if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
        raise InvalidDocument("Document must be between 1 byte and 6 MiB")

    if content_type == "application/pdf":
        if not re.match(rb"%PDF-[12]\.[0-9][\r\n]", data[:9]) or not data[-1024:].rstrip().endswith(b"%%EOF"):
            raise InvalidDocument("Document could not be read as a PDF")
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != IMAGE_FORMATS[content_type]:
                        raise InvalidDocument("Document type does not match its contents")
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise InvalidDocument("Document image dimensions are too large")
                    image.verify()
        except InvalidDocument:
            raise
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise InvalidDocument("Document could not be read as an image") from None

    return DocumentContent(content_type, len(data), hashlib.sha256(data).hexdigest())


def _private_bucket():
    bucket_id = settings.transaction_document_bucket
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", bucket_id)
        or bucket_id in {settings.storage_bucket, "listing-images", "property-network-private"}
    ):
        raise DocumentStorageUnavailable("Transaction document storage is not configured")

    try:
        storage = _get_client().storage
        bucket = storage.get_bucket(bucket_id)
        if (
            bucket.id != bucket_id
            or bucket.name != bucket_id
            or bucket.public is not False
            or type(bucket.file_size_limit) is not int
            or not 0 < bucket.file_size_limit <= MAX_DOCUMENT_BYTES
            or set(bucket.allowed_mime_types or ()) != DOCUMENT_TYPES
        ):
            raise DocumentStorageUnavailable("Transaction document storage is not configured")
        return storage.from_(bucket_id)
    except DocumentStorageUnavailable:
        raise
    except Exception:
        raise DocumentStorageUnavailable("Transaction document storage is unavailable") from None


def _object_key(room_id: UUID, document_id: UUID, version_id: UUID) -> str:
    return f"rooms/{room_id.hex}/{document_id.hex}/{version_id.hex}"


def _verify_bytes(data: bytes, expected: DocumentContent) -> bytes:
    if not isinstance(data, bytes) or not 0 < len(data) == expected.byte_count <= MAX_DOCUMENT_BYTES:
        raise DocumentIntegrityError("Transaction document size mismatch")
    if hashlib.sha256(data).hexdigest() != expected.sha256:
        raise DocumentIntegrityError("Transaction document digest mismatch")
    return data


def _read(bucket, key: str, expected: DocumentContent) -> bytes:
    try:
        data = bucket.download(key)
    except Exception:
        raise DocumentStorageUnavailable("Transaction document could not be read") from None
    return _verify_bytes(data, expected)


def store_document(
    room_id: UUID,
    document_id: UUID,
    version_id: UUID,
    data: bytes,
    expected: DocumentContent,
) -> None:
    """Store bytes for a reserved version, then confirm the exact object contents."""
    if expected.content_type not in DOCUMENT_TYPES:
        raise InvalidDocument("Unsupported document type")
    _verify_bytes(data, expected)
    bucket = _private_bucket()
    key = _object_key(room_id, document_id, version_id)
    try:
        bucket.upload(
            key,
            data,
            {"content-type": expected.content_type, "cache-control": "0", "x-upsert": "false"},
        )
    except Exception:
        # A duplicate or lost upload response is safe to retry by checking the reserved key.
        pass
    _read(bucket, key, expected)


def read_document(
    room_id: UUID,
    document_id: UUID,
    version_id: UUID,
    expected: DocumentContent,
) -> bytes:
    return _read(_private_bucket(), _object_key(room_id, document_id, version_id), expected)
