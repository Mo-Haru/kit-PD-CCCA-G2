"""ブラウザとテストが共有する暗号契約。

サーバーはこの定数を検証と文書化に使う。HPKEやAESの処理自体は行わない。
回答の平文、PRF出力、秘密鍵はここを経由してサーバーへ入らない。
"""

CRYPTO_VERSION = 1
SCHEMA_VERSION = 1
HPKE_SUITE = "DHKEM(X25519, HKDF-SHA256)/HKDF-SHA256/AES-128-GCM"
HPKE_KEM_ID = 0x0020
HPKE_KDF_ID = 0x0001
HPKE_AEAD_ID = 0x0001
DOCUMENT_INFO = "acp/document/v1"
WRAP_INFO = "acp/passkey-wrap/v1"
BUNDLE_INFO = "acp/key-bundle/v1"
OWNER_COPY_INFO = "acp/share-owner-copy/v1"
ANSWER_ITEM_STATES = ("answered", "undecided", "declined")

# クライアントがAADへ入れるUTF-8文字列。プレースホルダは実装と同じ順。
DOCUMENT_AAD = "acp/document/v1|{user_id}|{data_key_id}|{revision}|{schema}"
WRAP_AAD = "acp/passkey-wrap/v1|{user_handle}|{credential_id}|{data_key_id}"
BUNDLE_AAD = "acp/key-bundle/v1|{data_key_id}|{revision}"
OWNER_COPY_AAD = "acp/share-owner-copy/v1|{share_id}|{version}"
HPKE_AAD = "hpke-v1|{purpose}|{context_id}|{owner_id}|{recipient_id}|{key_id}|{version}"
