"""Download header helper: passive-type allowlist and safe Content-Disposition."""

from __future__ import annotations

import pytest

from mailsim.api.downloads import content_disposition, safe_content_type, safe_download_headers


@pytest.mark.parametrize(("given", "served"), [
    ("application/pdf", "application/pdf"), ("IMAGE/PNG", "image/png"),
    ("text/plain; charset=utf-8", "text/plain"), ("text/csv", "text/csv"),
    ("application/json", "application/json"), ("application/zip", "application/zip"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("text/html", "application/octet-stream"), ("image/svg+xml", "application/octet-stream"),
    ("application/xhtml+xml", "application/octet-stream"), ("text/xml", "application/octet-stream"),
    ("application/xml", "application/octet-stream"),
    ("application/javascript", "application/octet-stream"),
    ("text/javascript", "application/octet-stream"), ("", "application/octet-stream"),
    (None, "application/octet-stream"), ("garbage", "application/octet-stream"),
])  # fmt: skip
def test_only_passive_types_keep_their_media_type(given, served):
    assert safe_content_type(given) == served


def test_headers_and_dispositions():
    h = safe_download_headers("INV-001.pdf", "application/pdf")
    assert h == {"Content-Type": "application/pdf",
                 "Content-Disposition": "attachment; filename=\"INV-001.pdf\"; "
                                        "filename*=UTF-8''INV-001.pdf",
                 "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                 "Cache-Control": "private, no-store"}  # fmt: skip
    assert (
        content_disposition("")
        == "attachment; filename=\"attachment\"; filename*=UTF-8''attachment"
    )
    tricky = content_disposition('a"b\\c/d\r\n.txt')
    assert tricky == "attachment; filename=\"a_b_c_d.txt\"; filename*=UTF-8''a%22b_c_d.txt"
    assert content_disposition("résumé.pdf").endswith("filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
