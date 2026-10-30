"""Tests for CryptoHelper (RSA key exchange and AES-256-GCM encryption/decryption)."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from slave.crypto_helper import CryptoClient


def test_aes_gcm_roundtrip():
    client = CryptoClient()
    client.session_token = "test-token-12345"

    payload = {"testId": "student-101", "score": 100, "code": "print('ok')"}
    encrypted = client.encrypt_payload(payload)

    assert "iv" in encrypted
    assert "ciphertext" in encrypted
    assert "tag" in encrypted
    assert encrypted["device_uuid"] == client.device_uuid

    decrypted = client.decrypt_payload(
        iv_b64=encrypted["iv"],
        ciphertext_b64=encrypted["ciphertext"],
        tag_b64=encrypted["tag"],
    )

    assert decrypted["testId"] == "student-101"
    assert decrypted["score"] == 100
    assert decrypted["session_token"] == "test-token-12345"
    assert "timestamp" in decrypted
    assert "nonce" in decrypted


def test_rsa_aes_key_exchange():
    # Generate mock server RSA keypair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    client = CryptoClient()
    encrypted_hex = client.encrypt_aes_key_with_rsa(public_pem)
    assert len(encrypted_hex) > 0

    # Decrypt on mock server side
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    encrypted_bytes = bytes.fromhex(encrypted_hex)
    decrypted_aes_key = private_key.decrypt(
        encrypted_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    assert decrypted_aes_key == client.aes_key
