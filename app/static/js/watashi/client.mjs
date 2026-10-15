/**
 * 他担当が呼ぶ共通クライアント。
 * 平文・PRF出力・秘密鍵・完全なQR URLはAPIへ載せない。
 */
import { b64urlToBytes, bytesToB64url, randomBytes, randomShareId, randomUuid } from "./bytes.mjs";
import {
  buildAnswerDocument,
  canRegister,
  createShareUrl,
  decryptBundle,
  decryptDocument,
  decryptOwnerCopy,
  detectPrf,
  encryptBundle,
  encryptDocument,
  encryptOwnerCopy,
  generateHpkeKeyPair,
  hpkeAad,
  hpkeOpen,
  hpkeSeal,
  selectedShareItems,
  unwrapStorageKey,
  wrapStorageKey,
} from "./crypto.mjs";

const vault = {
  storageKey: null,
  dataKeyId: null,
  userId: null,
  receivePrivate: null,
  receivePublic: null,
  receiveKeyId: null,
};

export function getUnlockState() {
  return {
    authenticated: Boolean(vault.userId),
    unlocked: vault.storageKey instanceof Uint8Array,
  };
}

export function unlockedKeyMaterial() {
  if (!(vault.storageKey instanceof Uint8Array)) return null;
  return {
    storageKey: vault.storageKey,
    dataKeyId: vault.dataKeyId,
    userId: vault.userId,
  };
}

export function csrfToken() {
  const match = document.cookie.split("; ").find((item) => item.startsWith("wn_csrf="));
  return match ? decodeURIComponent(match.slice("wn_csrf=".length)) : "";
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const method = options.method || "GET";
  if (method !== "GET" && method !== "HEAD") headers.set("X-CSRF-Token", csrfToken());
  let body;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { method, headers, body, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.message || "操作できませんでした。");
    error.code = payload.error;
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function publicKeyRequest(publicKey) {
  const copy = structuredClone(publicKey);
  copy.challenge = b64urlToBytes(copy.challenge);
  if (copy.user) copy.user.id = b64urlToBytes(copy.user.id);
  for (const name of ["excludeCredentials", "allowCredentials"]) {
    if (Array.isArray(copy[name])) {
      copy[name] = copy[name].map((item) => ({ ...item, id: b64urlToBytes(item.id) }));
    }
  }
  const first = copy.extensions?.prf?.eval?.first;
  if (typeof first === "string") copy.extensions.prf.eval.first = b64urlToBytes(first);
  return copy;
}

function credentialJson(credential) {
  const response = credential.response;
  const common = {
    id: credential.id,
    rawId: bytesToB64url(new Uint8Array(credential.rawId)),
    type: credential.type,
  };
  if (response.attestationObject) {
    return {
      ...common,
      response: {
        clientDataJSON: bytesToB64url(new Uint8Array(response.clientDataJSON)),
        attestationObject: bytesToB64url(new Uint8Array(response.attestationObject)),
        transports: typeof response.getTransports === "function" ? response.getTransports() : [],
      },
    };
  }
  return {
    ...common,
    response: {
      clientDataJSON: bytesToB64url(new Uint8Array(response.clientDataJSON)),
      authenticatorData: bytesToB64url(new Uint8Array(response.authenticatorData)),
      signature: bytesToB64url(new Uint8Array(response.signature)),
      userHandle: response.userHandle ? bytesToB64url(new Uint8Array(response.userHandle)) : null,
    },
  };
}

function prfOutput(credential) {
  const first = credential.getClientExtensionResults?.().prf?.results?.first;
  if (!first) return null;
  return new Uint8Array(first);
}

function rememberBundle(bundle, keyId) {
  vault.receivePublic = b64urlToBytes(bundle.receive.publicKey);
  vault.receivePrivate = b64urlToBytes(bundle.receive.privateKey);
  vault.receiveKeyId = keyId || bundle.receive.keyId || null;
}

async function protectNewKey({ options, credential, prf }) {
  const storageKey = randomBytes(32);
  const salt = randomBytes(32);
  const nonce = randomBytes(12);
  const receive = await generateHpkeKeyPair();
  const bundleObject = { v: 1, receive: {
    publicKey: bytesToB64url(receive.publicKey),
    privateKey: bytesToB64url(receive.privateKey),
  } };
  const packed = await encryptBundle({
    storageKey,
    dataKeyId: options.data_key_id,
    revision: 1,
    plaintext: new TextEncoder().encode(JSON.stringify(bundleObject)),
  });
  const wrapped = await wrapStorageKey({
    prfOutput: prf,
    kdfSalt: salt,
    storageKey,
    nonce,
    userHandleB64: options.user_handle,
    credentialIdB64: credential.id,
    dataKeyId: options.data_key_id,
  });
  return {
    storageKey,
    receive,
    envelope: {
      prf_input: options.prf_input,
      kdf_salt: bytesToB64url(salt),
      wrapped_key: bytesToB64url(wrapped),
      nonce: bytesToB64url(nonce),
      kdf_version: 1,
      crypto_version: 1,
    },
    keyBundle: {
      ciphertext: bytesToB64url(packed.ciphertext),
      nonce: bytesToB64url(packed.nonce),
      crypto_version: 1,
    },
  };
}

export async function registerAccount(displayName) {
  const prf = await detectPrf();
  if (!canRegister(prf)) throw new Error(prf.reason);
  const options = await api("/api/v1/auth/register/options", { method: "POST", body: { display_name: displayName } });
  const credential = await navigator.credentials.create({ publicKey: publicKeyRequest(options.public_key) });
  const output = prfOutput(credential);
  if (!output) {
    throw new Error("このパスキーはPRFに対応していません。登録を完了していません。対応するパスキーで最初からやり直してください。");
  }
  const protectedKey = await protectNewKey({ options, credential, prf: output });
  const created = await api("/api/v1/auth/register/verify", {
    method: "POST",
    body: {
      credential: credentialJson(credential),
      envelope: protectedKey.envelope,
      encryption_public_key: bytesToB64url(protectedKey.receive.publicKey),
      key_bundle: protectedKey.keyBundle,
    },
  });
  vault.storageKey = protectedKey.storageKey;
  vault.dataKeyId = created.data_key_id;
  vault.userId = created.user_id;
  vault.receivePrivate = protectedKey.receive.privateKey;
  vault.receivePublic = protectedKey.receive.publicKey;
  vault.receiveKeyId = created.encryption_key_id;
  return created;
}

export async function addPasskey() {
  const prf = await detectPrf();
  if (!canRegister(prf)) throw new Error(prf.reason);
  if (!(vault.storageKey instanceof Uint8Array)) {
    throw new Error("先に今のパスキーで鍵を解除してください。新しいパスキーは、今ある保管用鍵を包み直します。");
  }
  const options = await api("/api/v1/auth/passkeys/options", { method: "POST", body: {} });
  const credential = await navigator.credentials.create({ publicKey: publicKeyRequest(options.public_key) });
  const output = prfOutput(credential);
  if (!output) {
    throw new Error("追加したパスキーはPRFに対応していません。サーバーには登録していません。");
  }
  const salt = randomBytes(32);
  const nonce = randomBytes(12);
  const wrapped = await wrapStorageKey({
    prfOutput: output,
    kdfSalt: salt,
    storageKey: vault.storageKey,
    nonce,
    userHandleB64: options.user_handle,
    credentialIdB64: credential.id,
    dataKeyId: options.data_key_id,
  });
  return api("/api/v1/auth/passkeys/verify", {
    method: "POST",
    body: {
      credential: credentialJson(credential),
      envelope: {
        prf_input: options.prf_input,
        kdf_salt: bytesToB64url(salt),
        wrapped_key: bytesToB64url(wrapped),
        nonce: bytesToB64url(nonce),
        kdf_version: 1,
        crypto_version: 1,
      },
    },
  });
}

export async function registerTransferredPasskey(transferToken, storageKey) {
  const prf = await detectPrf();
  if (!canRegister(prf)) throw new Error(prf.reason);
  const headers = { "X-Transfer-Token": transferToken };
  const options = await api("/api/v1/transfer/passkey/options", { method: "POST", headers });
  const credential = await navigator.credentials.create({ publicKey: publicKeyRequest(options.public_key) });
  const output = prfOutput(credential);
  if (!output) {
    throw new Error("このパスキーはPRFに対応していません。古いパスキーは無効化していません。");
  }
  const salt = randomBytes(32);
  const nonce = randomBytes(12);
  const wrapped = await wrapStorageKey({
    prfOutput: output,
    kdfSalt: salt,
    storageKey,
    nonce,
    userHandleB64: options.user_handle,
    credentialIdB64: credential.id,
    dataKeyId: options.data_key_id,
  });
  return api("/api/v1/transfer/passkey/verify", {
    method: "POST",
    headers,
    body: {
      credential: credentialJson(credential),
      envelope: {
        prf_input: options.prf_input,
        kdf_salt: bytesToB64url(salt),
        wrapped_key: bytesToB64url(wrapped),
        nonce: bytesToB64url(nonce),
        kdf_version: 1,
        crypto_version: 1,
      },
    },
  });
}

export async function login() {
  const options = await api("/api/v1/auth/login/options", { method: "POST", body: {} });
  const credential = await navigator.credentials.get({ publicKey: publicKeyRequest(options.public_key) });
  return api("/api/v1/auth/login/verify", { method: "POST", body: { credential: credentialJson(credential) } });
}

export async function unlock() {
  const options = await api("/api/v1/auth/unlock/options", { method: "POST", body: {} });
  const credential = await navigator.credentials.get({ publicKey: publicKeyRequest(options.public_key) });
  const output = prfOutput(credential);
  if (!output || !options.envelope) {
    throw new Error("鍵を解除できません。PRFに対応した、登録時と同じパスキーを使ってください。");
  }
  const envelope = options.envelope;
  const storageKey = await unwrapStorageKey({
    prfOutput: output,
    kdfSalt: b64urlToBytes(envelope.kdf_salt),
    wrappedKey: b64urlToBytes(envelope.wrapped_key),
    nonce: b64urlToBytes(envelope.nonce),
    userHandleB64: bytesToB64url(new Uint8Array(credential.response.userHandle)),
    credentialIdB64: credential.id,
    dataKeyId: envelope.data_key_id,
  });
  const session = await api("/api/v1/auth/session");
  if (options.key_bundle) {
    const opened = await decryptBundle({
      storageKey,
      dataKeyId: envelope.data_key_id,
      revision: options.key_bundle.revision,
      nonce: b64urlToBytes(options.key_bundle.nonce),
      ciphertext: b64urlToBytes(options.key_bundle.ciphertext),
    });
    rememberBundle(JSON.parse(new TextDecoder().decode(opened)), null);
    const keys = await api(`/api/v1/encryption-keys/${session.user_id}`);
    const active = keys.keys.find((item) => item.status === "active");
    if (active) vault.receiveKeyId = active.id;
  }
  await api("/api/v1/auth/unlock/verify", { method: "POST", body: { credential: credentialJson(credential) } });
  vault.storageKey = storageKey;
  vault.dataKeyId = envelope.data_key_id;
  vault.userId = session.user_id;
  return { ok: true };
}

export async function logout() {
  const result = await api("/api/v1/auth/logout", { method: "POST", body: {} });
  vault.storageKey = null;
  vault.dataKeyId = null;
  vault.userId = null;
  vault.receivePrivate = null;
  vault.receivePublic = null;
  vault.receiveKeyId = null;
  return result;
}

export async function saveAnswerDocument(items) {
  if (!vault.storageKey) throw new Error("先にパスキーで鍵を解除してください。");
  const current = await api("/api/v1/answers/current");
  const document = buildAnswerDocument(items);
  const next = current.revision + 1;
  const packed = await encryptDocument({
    storageKey: vault.storageKey,
    dataKeyId: vault.dataKeyId,
    userId: vault.userId,
    revision: next,
    schema: 1,
    plaintext: new TextEncoder().encode(JSON.stringify(document)),
  });
  return api("/api/v1/answers/current", {
    method: "PUT",
    body: {
      expected_revision: current.revision,
      data_key_id: vault.dataKeyId,
      nonce: bytesToB64url(packed.nonce),
      ciphertext: bytesToB64url(packed.ciphertext),
      crypto_version: 1,
      schema_version: 1,
    },
  });
}

export async function loadAnswerDocument() {
  if (!vault.storageKey) throw new Error("先にパスキーで鍵を解除してください。");
  const current = await api("/api/v1/answers/current");
  if (!current.document) return null;
  const opened = await decryptDocument({
    storageKey: vault.storageKey,
    dataKeyId: current.document.data_key_id,
    userId: vault.userId,
    revision: current.document.revision,
    schema: current.document.schema_version,
    nonce: b64urlToBytes(current.document.nonce),
    ciphertext: b64urlToBytes(current.document.ciphertext),
  });
  return JSON.parse(new TextDecoder().decode(opened));
}

export async function createFamilyShare({ connectionId, recipientKeyId, recipientUserId, selectedIds, expiresAt = null }) {
  if (!vault.storageKey || !vault.userId) throw new Error("先にパスキーで鍵を解除してください。");
  const document = await loadAnswerDocument();
  const payload = selectedShareItems(document || { schema: 1, items: {} }, selectedIds);
  const shareId = randomUuid();
  const keys = await api(`/api/v1/encryption-keys/${recipientUserId}`);
  const active = keys.keys.find((item) => item.id === recipientKeyId && item.status === "active");
  if (!active) {
    const error = new Error("相手の受信用公開鍵が変わっています。表示を確認してから共有してください。");
    error.code = "key_changed";
    throw error;
  }
  const aad = hpkeAad({
    purpose: "family",
    contextId: shareId,
    ownerId: vault.userId,
    recipientId: recipientUserId,
    keyId: recipientKeyId,
    version: 1,
  });
  const sealed = await hpkeSeal(b64urlToBytes(active.public_key), new TextEncoder().encode(JSON.stringify(payload)), aad);
  const owner = await encryptOwnerCopy({
    storageKey: vault.storageKey,
    dataKeyId: vault.dataKeyId,
    shareId,
    version: 1,
    plaintext: new TextEncoder().encode(JSON.stringify(payload)),
  });
  return api("/api/v1/family/shares", {
    method: "POST",
    body: {
      id: shareId,
      connection_id: connectionId,
      recipient_key_id: recipientKeyId,
      expires_at: expiresAt,
      confirmed: true,
      version: { hpke_enc: bytesToB64url(sealed.enc), ciphertext: bytesToB64url(sealed.ciphertext) },
      owner_copy: {
        data_key_id: vault.dataKeyId,
        nonce: bytesToB64url(owner.nonce),
        ciphertext: bytesToB64url(owner.ciphertext),
        crypto_version: 1,
      },
    },
  });
}

export async function openReceivedShare(share) {
  if (!vault.receivePrivate) throw new Error("先にパスキーで鍵を解除してください。");
  if (share.recipient_key_id !== vault.receiveKeyId) {
    throw new Error("この共有は別の受信用鍵で包まれています。鍵の変更を確認してください。");
  }
  const aad = hpkeAad({
    purpose: "family",
    contextId: share.id,
    ownerId: share.owner_user_id,
    recipientId: vault.userId,
    keyId: share.recipient_key_id,
    version: share.current_version,
  });
  const opened = await hpkeOpen(
    vault.receivePrivate,
    b64urlToBytes(share.current.hpke_enc),
    b64urlToBytes(share.current.ciphertext),
    aad,
  );
  return JSON.parse(new TextDecoder().decode(opened));
}

export async function openOwnerCopy(share) {
  const opened = await decryptOwnerCopy({
    storageKey: vault.storageKey,
    dataKeyId: share.owner_copy.data_key_id,
    shareId: share.id,
    version: share.owner_copy.version,
    nonce: b64urlToBytes(share.owner_copy.nonce),
    ciphertext: b64urlToBytes(share.owner_copy.ciphertext),
  });
  return JSON.parse(new TextDecoder().decode(opened));
}

export async function createQrShare({ mode, expiresAt = null }) {
  const pair = await generateHpkeKeyPair();
  const shareId = randomShareId();
  const startToken = randomBytes(32);
  const tokenHash = new Uint8Array(await crypto.subtle.digest("SHA-256", startToken));
  const aad = hpkeAad({
    purpose: "qr",
    contextId: shareId,
    ownerId: vault.userId,
    recipientId: "qr",
    keyId: bytesToB64url(pair.publicKey),
    version: 1,
  });
  const document = await loadAnswerDocument();
  const sealed = await hpkeSeal(pair.publicKey, new TextEncoder().encode(JSON.stringify(document || { schema: 1, items: {} })), aad);
  const created = await api("/api/v1/qr-shares", {
    method: "POST",
    body: {
      id: shareId,
      mode,
      expires_at: expiresAt,
      public_key: bytesToB64url(pair.publicKey),
      start_token_hash: bytesToB64url(tokenHash),
      version: { hpke_enc: bytesToB64url(sealed.enc), ciphertext: bytesToB64url(sealed.ciphertext) },
    },
  });
  return {
    share: created.share,
    url: createShareUrl({ origin: location.origin, shareId, privateKey: pair.privateKey, startToken }),
  };
}

export { createShareUrl, detectPrf, selectedShareItems };
