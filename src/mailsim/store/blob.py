"""Content-addressed blob storage for raw MIME and attachments."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Protocol

from mailsim.domain.errors import InvalidRequest, NotFound

_KEY = re.compile(r"^[0-9a-f]{64}$")


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class FilesystemBlobStore:
    """Keys are sha256 hex of the content: idempotent writes, tamper-evident reads."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):  # also blocks path traversal
            raise InvalidRequest(f"invalid blob key {key!r}")
        return self.root / key[:2] / key

    def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        path = self._path(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFound(f"blob {key} not found")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != key:
            raise InvalidRequest(f"blob {key} failed integrity check")
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
