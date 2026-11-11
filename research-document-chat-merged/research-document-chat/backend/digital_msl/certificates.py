"""Registration certificate upload: type check by magic bytes, malware scan hook, encrypted storage."""
from __future__ import annotations

import logging
import socket
import struct
import uuid
from pathlib import Path

from digital_msl import security
from digital_msl.config import BACKEND_DIR, get_dmsl_settings

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
UPLOAD_DIR = BACKEND_DIR / "uploads" / "certificates"

_SIGNATURES = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)


def sniff(data: bytes) -> tuple[str, str]:
    for magic, media, ext in _SIGNATURES:
        if data.startswith(magic):
            return media, ext
    raise ValueError("Upload a PDF, PNG or JPEG file.")


def scan(data: bytes) -> str:
    """Return 'clean', 'infected' or 'unscanned'. ClamAV (clamd INSTREAM) is used when configured;
    otherwise only the type and size checks run, and the file is recorded as 'unscanned'."""
    host = (get_dmsl_settings().clamav_host or "").strip()
    if not host:
        return "unscanned"
    try:
        with socket.create_connection((host, get_dmsl_settings().clamav_port), timeout=10) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 8192):
                chunk = data[i : i + 8192]
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
            reply = sock.recv(4096).decode("utf-8", "ignore")
        return "infected" if "FOUND" in reply else ("clean" if "OK" in reply else "unscanned")
    except Exception as exc:
        logger.warning("ClamAV scan unavailable: %s", type(exc).__name__)
        return "unscanned"


def store_certificate(data: bytes) -> tuple[str, str, str]:
    """Validate, scan and encrypt. Returns (relative_path, scan_status, media_type)."""
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError("The file is larger than 5 MB.")
    media, _ext = sniff(data)
    status = scan(data)
    if status == "infected":
        raise ValueError("The file was rejected by the malware scan.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.bin"
    (UPLOAD_DIR / name).write_bytes(security.encrypt_bytes(data))
    return f"{media}|{name}", status, media


def read_certificate(stored: str) -> tuple[bytes, str]:
    media, _, name = stored.partition("|")
    path = (UPLOAD_DIR / Path(name).name)
    return security.decrypt_bytes(path.read_bytes()), media
