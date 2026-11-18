"""In-memory private image storage for the isolated browser rehearsal."""

from contextlib import contextmanager
import re
from threading import Lock
from unittest.mock import patch


BUCKET = "property-network-private"
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_OBJECTS = 12
MAX_STORED_BYTES = 12 * 1024 * 1024
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(rf"network/{_UUID}/{_UUID}\.webp")
_PHOTO_ID = re.compile(_UUID)


class PrivateStorageFixture:
    def __init__(self):
        # Supabase exposes its bucket operations through client.storage.
        self.storage = self
        self._lock = Lock()
        self._objects = {}
        self._stored_bytes = 0
        self._bucket_reads = 0
        self._bucket_selections = 0
        self._uploads = 0
        self._downloads = 0
        self._closed = False

    def _require_bucket(self, name):
        if self._closed or name != BUCKET:
            raise RuntimeError("Unexpected private storage bucket access")

    def _require_key(self, key):
        if self._closed or not isinstance(key, str) or not _KEY.fullmatch(key):
            raise RuntimeError("Unexpected private storage object access")

    def get_bucket(self, name):
        with self._lock:
            self._require_bucket(name)
            self._bucket_reads += 1
            return {
                "id": BUCKET,
                "name": BUCKET,
                "public": False,
                "file_size_limit": MAX_PHOTO_BYTES,
                "allowed_mime_types": ["image/webp"],
            }

    def from_(self, name):
        with self._lock:
            self._require_bucket(name)
            self._bucket_selections += 1
            return self

    def upload(self, key, data, options):
        with self._lock:
            self._require_key(key)
            if options != {"content-type": "image/webp", "upsert": "false"}:
                raise RuntimeError("Unexpected private storage upload options")
            if (type(data) is not bytes or not 12 <= len(data) <= MAX_PHOTO_BYTES
                    or data[:4] != b"RIFF" or data[8:12] != b"WEBP"
                    or int.from_bytes(data[4:8], "little") != len(data) - 8):
                raise RuntimeError("Invalid private WebP upload")
            if (key in self._objects or len(self._objects) >= MAX_OBJECTS
                    or self._stored_bytes + len(data) > MAX_STORED_BYTES):
                raise RuntimeError("Private storage fixture capacity or immutability check failed")
            self._objects[key] = data
            self._stored_bytes += len(data)
            self._uploads += 1

    def download(self, key):
        with self._lock:
            self._require_key(key)
            if key not in self._objects:
                raise RuntimeError("Private storage fixture object does not exist")
            self._downloads += 1
            return self._objects[key]

    def tamper_photo(self, photo_id):
        """Flip one byte without changing length so the app must check the digest."""
        with self._lock:
            if self._closed or not isinstance(photo_id, str) or not _PHOTO_ID.fullmatch(photo_id):
                raise RuntimeError("Unexpected private storage fixture photo ID")
            matches = [key for key in self._objects if key.endswith(f"/{photo_id}.webp")]
            if len(matches) != 1:
                raise RuntimeError("Expected exactly one synthetic photo to tamper with")
            key = matches[0]
            original = self._objects[key]
            self._objects[key] = original[:-1] + bytes([original[-1] ^ 1])

    def snapshot(self):
        """Return counts without exposing object keys or image bytes."""
        with self._lock:
            return {
                "bucket_reads": self._bucket_reads,
                "bucket_selections": self._bucket_selections,
                "uploads": self._uploads,
                "downloads": self._downloads,
                "object_count": len(self._objects),
                "stored_bytes": self._stored_bytes,
            }

    def _close(self):
        with self._lock:
            self._closed = True
            self._objects.clear()
            self._stored_bytes = 0


@contextmanager
def install_private_storage_fixture():
    """Replace only the private media module's vendor client while the fixture runs."""
    from config import settings
    from utils import private_network_media

    if (settings.environment == "production" or settings.supabase_url or settings.supabase_service_key
            or private_network_media.BUCKET != BUCKET or private_network_media.MAX_BYTES != MAX_PHOTO_BYTES):
        raise RuntimeError("Private storage fixture requires a synthetic app with no Supabase credentials")
    fixture = PrivateStorageFixture()
    try:
        with patch.object(private_network_media, "_get_client", new=lambda: fixture):
            yield fixture
    finally:
        fixture._close()
