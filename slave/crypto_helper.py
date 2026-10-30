"""Cryptographic helper for RSA-OAEP and AES-256-GCM encryption compatible with NTUT Exam host."""

import base64
import json
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_pem_public_key


class CryptoClient:
    def __init__(self, device_uuid: Optional[str] = None):
        self.device_uuid = device_uuid or str(uuid.uuid4())
        self.aes_key = AESGCM.generate_key(bit_length=256)  # 32 bytes
        self.aesgcm = AESGCM(self.aes_key)
        self.session_token: Optional[str] = None

    def encrypt_aes_key_with_rsa(self, rsa_pem: str) -> str:
        """Encrypts 32-byte AES key using server's RSA Public Key with OAEP SHA-256."""
        pub_key = load_pem_public_key(rsa_pem.encode("utf-8"))
        encrypted = pub_key.encrypt(
            self.aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return encrypted.hex()

    def encrypt_payload(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Encrypts a dictionary into {iv, ciphertext, tag, device_uuid} using AES-256-GCM."""
        # Include session token, timestamp and nonce if authenticated
        payload = dict(data)
        if self.session_token and "session_token" not in payload:
            payload["session_token"] = self.session_token
        if "timestamp" not in payload:
            payload["timestamp"] = int(time.time() * 1000)
        if "nonce" not in payload:
            payload["nonce"] = str(uuid.uuid4())

        plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        iv = os.urandom(12)  # Standard 12-byte IV for GCM

        # cryptography AESGCM.encrypt appends 16-byte authentication tag to ciphertext
        ciphertext_and_tag = self.aesgcm.encrypt(iv, plaintext, None)
        ciphertext = ciphertext_and_tag[:-16]
        tag = ciphertext_and_tag[-16:]

        return {
            "device_uuid": self.device_uuid,
            "iv": base64.b64encode(iv).decode("utf-8"),
            "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
            "tag": base64.b64encode(tag).decode("utf-8"),
        }

    def decrypt_payload(self, iv_b64: str, ciphertext_b64: str, tag_b64: str) -> Dict[str, Any]:
        """Decrypts {iv, ciphertext, tag} using AES-256-GCM."""
        iv = base64.b64decode(iv_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
        tag = base64.b64decode(tag_b64)

        ciphertext_and_tag = ciphertext + tag
        decrypted_bytes = self.aesgcm.decrypt(iv, ciphertext_and_tag, None)
        return json.loads(decrypted_bytes.decode("utf-8"))
