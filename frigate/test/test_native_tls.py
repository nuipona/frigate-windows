import tempfile
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from frigate.util.tls import ensure_tls_certificate


class TestNativeTls(unittest.TestCase):
    def test_generates_persistent_self_signed_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / "tls" / "fullchain.pem"
            key = Path(directory) / "tls" / "privkey.pem"

            cert_path, key_path = ensure_tls_certificate(str(cert), str(key))

            parsed_cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
            parsed_key = serialization.load_pem_private_key(
                Path(key_path).read_bytes(), password=None
            )
            self.assertEqual(
                parsed_cert.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
                parsed_key.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
            )

            original_cert = cert.read_bytes()
            original_key = key.read_bytes()
            ensure_tls_certificate(str(cert), str(key))
            self.assertEqual(cert.read_bytes(), original_cert)
            self.assertEqual(key.read_bytes(), original_key)

    def test_rejects_partial_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / "fullchain.pem"
            key = Path(directory) / "privkey.pem"
            cert.write_text("certificate", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "requires both"):
                ensure_tls_certificate(str(cert), str(key))


if __name__ == "__main__":
    unittest.main()
