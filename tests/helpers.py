"""Shared test builders."""

from __future__ import annotations

import base64

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

PLAIN = (
    b"Message-ID: <invoice-001@example.test>\r\n"
    b"Date: Thu, 15 Oct 2026 09:00:00 +0000\r\n"
    b"From: Example Vendor <vendor@example.test>\r\n"
    b"To: finance@example.test\r\n"
    b"Cc: Procurement <procurement@example.test>\r\n"
    b"Subject: Invoice INV-001\r\n"
    b"\r\n"
    b"Please confirm the payment date.\r\n"
)

REPLY = (
    b"Message-ID: <reply-1@example.test>\r\n"
    b"In-Reply-To: <invoice-001@example.test>\r\n"
    b"References: <invoice-001@example.test>\r\n"
    b"Date: Thu, 15 Oct 2026 10:00:00 +0000\r\n"
    b"From: finance@example.test\r\n"
    b"To: vendor@example.test\r\n"
    b"Subject: Re: Invoice INV-001\r\n"
    b"\r\n"
    b"Will confirm.\r\n"
)

LATIN1 = (
    b"Message-ID: <latin1@example.test>\r\n"
    b"From: a@example.test\r\nTo: b@example.test\r\n"
    b"Subject: =?ISO-8859-1?Q?Caf=E9_invoice?=\r\n"
    b"Content-Type: text/plain; charset=iso-8859-1\r\n"
    b"Content-Transfer-Encoding: 8bit\r\n\r\n"
    b"caf\xe9\r\n"
)

BAD_CHARSET = (
    b"Message-ID: <badcs@example.test>\r\n"
    b"From: a@example.test\r\nTo: b@example.test\r\nSubject: x\r\n"
    b"Content-Type: text/plain; charset=no-such-charset\r\n\r\nhello\r\n"
)


def multipart_part(parts: list[bytes], subtype: str = "mixed", boundary: str = "BND") -> bytes:
    """A multipart entity (headers + body) usable as a nested part."""
    head = f'Content-Type: multipart/{subtype}; boundary="{boundary}"\r\n\r\n'.encode()
    body = b""
    for part in parts:
        body += b"--" + boundary.encode() + b"\r\n" + part + b"\r\n"
    return head + body + b"--" + boundary.encode() + b"--"


def multipart(parts: list[bytes], subtype: str = "mixed", boundary: str = "BND") -> bytes:
    head = (
        b"Message-ID: <mp@example.test>\r\nFrom: a@example.test\r\nTo: b@example.test\r\n"
        b"Subject: multipart\r\nMIME-Version: 1.0\r\n"
    )
    return head + multipart_part(parts, subtype, boundary) + b"\r\n"


def text_part(text: str, subtype: str = "plain") -> bytes:
    return f"Content-Type: text/{subtype}; charset=utf-8\r\n\r\n{text}".encode()


def attachment_part(
    filename: str,
    ctype: str,
    content: bytes,
    disposition: str = "attachment",
    cid: str | None = None,
) -> bytes:
    headers = (
        f'Content-Type: {ctype}\r\nContent-Disposition: {disposition}; filename="{filename}"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
    )
    if cid:
        headers += f"Content-ID: {cid}\r\n"
    return headers.encode() + b"\r\n" + base64.encodebytes(content).replace(b"\n", b"\r\n")
