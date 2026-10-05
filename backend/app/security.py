import json

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class CredentialCipher:
    def __init__(self, key: str | None = None) -> None:
        encryption_key = key or get_settings().token_encryption_key
        if not encryption_key:
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is required. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._fernet = Fernet(encryption_key.encode())

    def encrypt(self, payload: dict) -> str:
        return self._fernet.encrypt(json.dumps(payload).encode()).decode()

    def decrypt(self, token: str) -> dict:
        try:
            return json.loads(self._fernet.decrypt(token.encode()).decode())
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise RuntimeError("Stored Google credentials could not be decrypted") from exc

