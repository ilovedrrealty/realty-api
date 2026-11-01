"""Private immutable image objects. Provisioning is a separate reviewed step."""
import hashlib
import io
import warnings
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from utils.storage import _get_client

BUCKET = "property-network-private"
MAX_BYTES = 5 * 1024 * 1024


def prepare_image(data):
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "Photo must be under 5 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(415, "Only JPEG, PNG and WebP photos are supported")
                if image.width * image.height > 20_000_000:
                    raise HTTPException(413, "Photo dimensions are too large")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1800, 1800))
                image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, "WEBP", quality=88)
                result = output.getvalue()
                if len(result) > MAX_BYTES:
                    raise HTTPException(413, "Photo exceeds the storage limit")
                return result, image.width, image.height
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(415, "Photo could not be read") from None


def _private_bucket():
    client = _get_client()
    bucket = client.storage.get_bucket(BUCKET)
    public = bucket.get("public") if isinstance(bucket, dict) else getattr(bucket, "public", None)
    if public is not False:
        raise RuntimeError("Private network bucket is not configured")
    return client.storage.from_(BUCKET)


def store_image(object_key, data):
    # Never create a bucket, publish an object, overwrite bytes or return a URL.
    _private_bucket().upload(object_key, data, {"content-type": "image/webp", "upsert": "false"})


def read_image(row):
    data = _private_bucket().download(row.object_key)
    if not isinstance(data, bytes) or len(data) != row.byte_count or len(data) > MAX_BYTES:
        raise RuntimeError("Private image size mismatch")
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise RuntimeError("Private image digest mismatch")
    return data
