"""TLS for the INBOUND protocol listeners (IMAP STARTTLS, SMTP STARTTLS). Server side only.

This is the single module allowed to import `ssl` (ruff per-file exemption, enforced by
tests/integration/test_safety.py). It builds *server* contexts only; nothing here can open a
connection. Two sources of certificates:

- an operator-supplied PEM certificate + key (`server_context(cert, key)`), or
- a throw-away self-signed certificate for local use (`self_signed_context()`), generated
  with `cryptography` (a runtime dependency via `mcp` -> `pyjwt[crypto]`).
"""

from __future__ import annotations

import ssl
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

TLSContext = ssl.SSLContext
"""Re-exported so other protocol modules can annotate contexts without importing `ssl`."""


def server_context(certfile: str | Path, keyfile: str | Path | None = None) -> TLSContext:
    """A TLS 1.2+ server context from PEM files."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(certfile), str(keyfile) if keyfile is not None else None)
    return ctx


def self_signed_pem(hostname: str = "localhost") -> tuple[bytes, bytes]:
    """(certificate PEM, private key PEM) for a short-lived self-signed certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def self_signed_context(hostname: str = "localhost") -> TLSContext:
    """A server context with a freshly generated self-signed certificate (local use only)."""
    cert_pem, key_pem = self_signed_pem(hostname)
    with tempfile.TemporaryDirectory(prefix="mailsim-tls-") as tmp:
        cert_path, key_path = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)
        key_path.chmod(0o600)
        return server_context(cert_path, key_path)


__all__ = ["TLSContext", "self_signed_context", "self_signed_pem", "server_context"]
