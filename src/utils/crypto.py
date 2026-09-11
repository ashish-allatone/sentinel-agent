import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

VALID_ENGINES = {"postgresql", "mysql", "mariadb", "oracle", "redis", "mongodb" , "sqlserver" ,
                 "nginx" , "apache" , "httpd" , "nginx.exe" , "jboss" , "wildfly"}
_ALIAS = {"postgres": "postgresql", "pg": "postgresql", "psql": "postgresql",
          "mongo": "mongodb"}

_fernet: Optional[Fernet] = None


def canon_engine(e: str) -> str:
    e = str(e or "").strip().lower()
    return _ALIAS.get(e, e)


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.getenv("CRED_ENC_KEY")
        if not key:
            raise RuntimeError(
                "CRED_ENC_KEY not set — refusing to store passwords unencrypted."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    if not plaintext:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: Optional[str]) -> Optional[str]:
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("cannot decrypt password — CRED_ENC_KEY does not "
                           "match the key used to encrypt this row")