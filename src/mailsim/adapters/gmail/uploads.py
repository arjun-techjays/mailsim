"""Media uploads for ``/upload/gmail/v1/...``: ``uploadType=media`` (the body is the RFC 822
message) and ``uploadType=multipart`` (multipart/related: JSON metadata + the message).
Resumable uploads are not supported (UNSUPPORTED_OPERATION)."""

from __future__ import annotations

import base64
import binascii
import itertools
import json
import re
from email.parser import BytesHeaderParser
from typing import Any

from mailsim.domain.errors import InvalidRequest, UnsupportedOperation

_BOUNDARY = re.compile(r'boundary="?([^";]+)"?', re.IGNORECASE)


def _eol(data: bytes) -> bytes:
    """Drop one leading line ending."""
    return data[2:] if data.startswith(b"\r\n") else data.removeprefix(b"\n")


def _split(chunk: bytes) -> tuple[bytes, bytes]:
    chunk = _eol(chunk)  # the end of the delimiter line
    crlf, lf = chunk.find(b"\r\n\r\n"), chunk.find(b"\n\n")
    if chunk.startswith((b"\r\n", b"\n")):  # no part headers
        head, content = b"", _eol(chunk)
    elif crlf >= 0 and (lf < 0 or crlf <= lf):
        head, content = chunk[:crlf], chunk[crlf + 4 :]
    elif lf >= 0:
        head, content = chunk[:lf], chunk[lf + 2 :]
    else:
        raise InvalidRequest("malformed multipart upload part")
    content = content[:-2] if content.endswith(b"\r\n") else content.removesuffix(b"\n")
    return head, content


def split_multipart(body: bytes, content_type: str) -> tuple[dict[str, Any], bytes]:
    match = _BOUNDARY.search(content_type)
    if match is None:
        raise InvalidRequest("multipart upload without a boundary")
    chunks = body.split(b"--" + match.group(1).encode())[1:]
    parts = [_split(c) for c in itertools.takewhile(lambda c: not c.startswith(b"--"), chunks)]
    if len(parts) != 2:
        raise InvalidRequest("a multipart upload needs exactly a JSON part and a message part")
    try:
        meta = json.loads(parts[0][1] or b"{}")
    except ValueError:
        raise InvalidRequest("the multipart metadata part is not JSON") from None
    if not isinstance(meta, dict):
        raise InvalidRequest("the multipart metadata part must be a JSON object")
    headers = BytesHeaderParser().parsebytes(parts[1][0] + b"\r\n\r\n")
    raw = parts[1][1]
    if str(headers.get("Content-Transfer-Encoding", "")).strip().lower() == "base64":
        try:
            raw = base64.b64decode(raw)
        except (binascii.Error, ValueError):
            raise InvalidRequest("the message part is not valid base64") from None
    return meta, raw


def read_upload(
    upload_type: str | None, content_type: str, body: bytes
) -> tuple[dict[str, Any], bytes]:
    kind = (upload_type or "").lower()
    if kind == "resumable":
        raise UnsupportedOperation("resumable uploads are not supported; use uploadType=media "
                                   "or uploadType=multipart")  # fmt: skip
    if kind == "multipart" or (not kind and content_type.lower().startswith("multipart/")):
        return split_multipart(body, content_type)
    if kind not in ("", "media"):
        raise InvalidRequest(f"unknown uploadType {upload_type!r}")
    if not body.strip():
        raise InvalidRequest("empty upload")
    return {}, body
