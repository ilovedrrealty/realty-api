"""Dedicated, immutable public business photos; never deletes account avatars."""
import io
import re
import uuid
import warnings
from urllib.parse import urlsplit
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from config import settings
from utils.storage import _get_client

MAX_PHOTO_BYTES = 5 * 1024 * 1024


def prepare_photo(data):
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo must be under 5 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP"):
                    raise HTTPException(415, "Only JPEG, PNG or WebP photos are allowed")
                if image.width * image.height > 20_000_000:
                    raise HTTPException(413, "Photo dimensions are too large")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1600, 1600))
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                output = io.BytesIO()
                image.save(output, "WEBP", quality=88)
                return output.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(415, "Photo could not be read as a supported image")


def upload_professional_photo(data, user_id):
    client = _get_client()
    path = f"professionals/{user_id}/{uuid.uuid4()}.webp"
    client.storage.from_(settings.storage_bucket).upload(path, data, {"content-type": "image/webp", "upsert": "false"})
    return client.storage.from_(settings.storage_bucket).get_public_url(path)


def validate_owned_photo(url, user_id):
    if not url:
        return
    base = settings.supabase_url.rstrip("/")
    prefix = f"{base}/storage/v1/object/public/{settings.storage_bucket}/professionals/{user_id}/"
    parsed = urlsplit(url)
    suffix = url[len(prefix):] if base and url.startswith(prefix) else ""
    if parsed.query or parsed.fragment or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.webp", suffix):
        raise HTTPException(422, "Use a photo uploaded for your own professional profile")
