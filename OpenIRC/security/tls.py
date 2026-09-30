"""Strict TLS listener configuration."""
from pathlib import Path
import ssl


def create_tls_context(certificate: str, private_key: str) -> ssl.SSLContext:
    for label, filename in (("certificate", certificate), ("private key", private_key)):
        if not filename or not Path(filename).is_file():
            raise ValueError(f"TLS {label} file does not exist")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(certificate, private_key, password=lambda: "")
    except (ssl.SSLError, OSError) as exc:
        raise ValueError("Unable to load TLS certificate/private key: check PEM format, matching keys, and file permissions") from exc
    return context


create_server_context = create_tls_context
