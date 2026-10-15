"""テスト用のソフトウェア認証器。実在のパスキーや個人情報は使わない。"""

from __future__ import annotations

import hashlib
import json
import secrets

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.services.support import b64decode, b64encode


class SoftPasskey:
    def __init__(self, *, synced: bool = True):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.user_handle = b""
        self.synced = synced
        self.sign_count = 0

    def register(self, public_key: dict, origin: str) -> dict:
        self.user_handle = b64decode(public_key["user"]["id"], exact=32)
        flags = 0x5D if self.synced else 0x45
        auth_data = self._auth_data(public_key["rp"]["id"], flags, self.sign_count, include_credential=True)
        return self._credential(
            "webauthn.create",
            public_key["challenge"],
            origin,
            attestation_object=cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data}),
        )

    def authenticate(self, public_key: dict, origin: str, *, sign_count: int | None = None) -> dict:
        if sign_count is None:
            if not self.synced:
                self.sign_count += 1
            count = self.sign_count
        else:
            count = sign_count
        flags = 0x1D if self.synced else 0x05
        auth_data = self._auth_data(public_key["rpId"], flags, count, include_credential=False)
        client_data = self._client_data("webauthn.get", public_key["challenge"], origin)
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        body = self._credential("webauthn.get", public_key["challenge"], origin, authenticator_data=auth_data, signature=signature)
        body["response"]["userHandle"] = b64encode(self.user_handle)
        return body

    def _auth_data(self, rp_id: str, flags: int, sign_count: int, *, include_credential: bool) -> bytes:
        data = hashlib.sha256(rp_id.encode("utf-8")).digest()
        data += bytes([flags]) + sign_count.to_bytes(4, "big")
        if include_credential:
            numbers = self.private_key.public_key().public_numbers()
            cose = cbor2.dumps({
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            })
            data += bytes(16) + len(self.credential_id).to_bytes(2, "big") + self.credential_id + cose
        return data

    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode("utf-8")

    def _credential(self, kind: str, challenge: str, origin: str, **response_parts) -> dict:
        client_data = self._client_data(kind, challenge, origin)
        response = {"clientDataJSON": b64encode(client_data)}
        if "attestation_object" in response_parts:
            response["attestationObject"] = b64encode(response_parts["attestation_object"])
            response["transports"] = ["internal"]
        else:
            response["authenticatorData"] = b64encode(response_parts["authenticator_data"])
            response["signature"] = b64encode(response_parts["signature"])
        credential_id = b64encode(self.credential_id)
        return {"id": credential_id, "rawId": credential_id, "type": "public-key", "response": response}
