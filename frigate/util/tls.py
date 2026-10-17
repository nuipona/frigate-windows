"""TLS certificate handling for the native Windows web gateway."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

_CERTIFICATE_NAME = "fullchain.pem"
_PRIVATE_KEY_NAME = "privkey.pem"


def default_tls_paths(config_dir: str) -> tuple[str, str]:
    """Return the conventional persistent certificate and key paths."""
    tls_dir = os.path.join(config_dir, "tls")
    return (
        os.path.join(tls_dir, _CERTIFICATE_NAME),
        os.path.join(tls_dir, _PRIVATE_KEY_NAME),
    )


def _subject_alt_names() -> list[x509.GeneralName]:
    """Build SANs that are useful on the local Windows host.

    A user-supplied certificate remains the right choice for a stable LAN name.
    The generated certificate covers localhost, the current computer name, and
    addresses resolvable for that name at generation time.
    """
    hostname = socket.gethostname()
    dns_names = {"localhost", hostname}
    ip_addresses = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}

    try:
        fqdn = socket.getfqdn()
        if fqdn:
            dns_names.add(fqdn)
    except OSError:
        pass

    try:
        for result in socket.getaddrinfo(hostname, None):
            address = result[4][0].split("%", 1)[0]
            try:
                ip_addresses.add(ipaddress.ip_address(address))
            except ValueError:
                continue
    except OSError:
        pass

    return [x509.DNSName(name) for name in sorted(dns_names)] + [
        x509.IPAddress(address)
        for address in sorted(
            ip_addresses, key=lambda value: (value.version, int(value))
        )
    ]


def _write_new_certificate(cert_file: Path, key_file: Path) -> None:
    """Generate a persistent self-signed certificate pair."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    hostname = socket.gethostname() or "localhost"
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Frigate Native Windows"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(_subject_alt_names()), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Replace complete files so an interrupted write cannot leave uvicorn with
    # a truncated key pair. The containing config directory is ACL-protected by
    # the Windows installer.
    cert_temp = cert_file.with_suffix(cert_file.suffix + ".tmp")
    key_temp = key_file.with_suffix(key_file.suffix + ".tmp")
    cert_temp.write_bytes(cert_bytes)
    key_temp.write_bytes(key_bytes)
    os.replace(cert_temp, cert_file)
    os.replace(key_temp, key_file)


def ensure_tls_certificate(cert_path: str, key_path: str) -> tuple[str, str]:
    """Return a usable TLS pair, generating the default pair when absent.

    Existing files are treated as administrator-managed and are never
    overwritten. A partial pair is rejected rather than silently replacing a
    certificate or private key.
    """
    cert_file = Path(cert_path).expanduser().resolve()
    key_file = Path(key_path).expanduser().resolve()

    cert_exists = cert_file.is_file()
    key_exists = key_file.is_file()

    if cert_exists != key_exists:
        raise FileNotFoundError(
            "TLS requires both certificate and private-key files; "
            f"certificate={cert_file}, key={key_file}"
        )

    if cert_exists:
        return str(cert_file), str(key_file)

    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info("No TLS certificate was found; generating a self-signed certificate")
    _write_new_certificate(cert_file, key_file)
    return str(cert_file), str(key_file)
