"""Шифрование API-секретов в базе. Ключ — ENCRYPTION_KEY из .env или файл data/secret.key."""
import os
from pathlib import Path

from cryptography.fernet import Fernet

from .config import config


def _load_key() -> bytes:
    if os.getenv("ENCRYPTION_KEY"):
        return os.environ["ENCRYPTION_KEY"].encode()
    path = Path(config.db_path).parent / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return path.read_bytes().strip()


_fernet = Fernet(_load_key())


def encrypt(text: str) -> str:
    return _fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
