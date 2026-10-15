/**
 * ブラウザ内の暗号。サーバーへ平文・PRF出力・秘密鍵を送らない。
 * HPKEは同一オリジンの hpke-js 1.8.0 を使う。
 */
import { AeadId, CipherSuite, KdfId, KemId } from "../../vendor/hpke-js-1.8.0.esm.mjs";
import { b64urlToBytes, bytesToB64url, copyBytes, randomBytes } from "./bytes.mjs";

const text = new TextEncoder();

export function suite() {
  return new CipherSuite({
    kem: KemId.DhkemX25519HkdfSha256,
    kdf: KdfId.HkdfSha256,
    aead: AeadId.Aes128Gcm,
  });
}

export async function hkdfSha256(ikm, salt, info, length = 32) {
  const key = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt, info: typeof info === "string" ? text.encode(info) : info },
    key,
    length * 8,
  );
  return new Uint8Array(bits);
}

async function aesKey(raw) {
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function aesGcmEncrypt(keyBytes, nonce, plaintext, aad) {
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: aad },
    await aesKey(keyBytes),
    plaintext,
  );
  return new Uint8Array(encrypted);
}

export async function aesGcmDecrypt(keyBytes, nonce, ciphertext, aad) {
  const opened = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: nonce, additionalData: aad },
    await aesKey(keyBytes),
    ciphertext,
  );
  return new Uint8Array(opened);
}

export function wrapAad(userHandleB64, credentialIdB64, dataKeyId) {
  return text.encode(`acp/passkey-wrap/v1|${userHandleB64}|${credentialIdB64}|${dataKeyId}`);
}

export async function wrapStorageKey({ prfOutput, kdfSalt, storageKey, nonce, userHandleB64, credentialIdB64, dataKeyId }) {
  const key = await hkdfSha256(prfOutput, kdfSalt, "acp/passkey-wrap/v1");
  return aesGcmEncrypt(key, nonce, storageKey, wrapAad(userHandleB64, credentialIdB64, dataKeyId));
}

export async function unwrapStorageKey({ prfOutput, kdfSalt, wrappedKey, nonce, userHandleB64, credentialIdB64, dataKeyId }) {
  const key = await hkdfSha256(prfOutput, kdfSalt, "acp/passkey-wrap/v1");
  return aesGcmDecrypt(key, nonce, wrappedKey, wrapAad(userHandleB64, credentialIdB64, dataKeyId));
}

export function documentAad(userId, dataKeyId, revision, schema) {
  return text.encode(`acp/document/v1|${userId}|${dataKeyId}|${revision}|${schema}`);
}

export async function encryptDocument({ storageKey, dataKeyId, userId, revision, schema, plaintext }) {
  const key = await hkdfSha256(storageKey, new Uint8Array(32), `acp/document/v1|${dataKeyId}`);
  const nonce = randomBytes(12);
  const ciphertext = await aesGcmEncrypt(key, nonce, plaintext, documentAad(userId, dataKeyId, revision, schema));
  return { nonce, ciphertext };
}

export async function decryptDocument({ storageKey, dataKeyId, userId, revision, schema, nonce, ciphertext }) {
  const key = await hkdfSha256(storageKey, new Uint8Array(32), `acp/document/v1|${dataKeyId}`);
  return aesGcmDecrypt(key, nonce, ciphertext, documentAad(userId, dataKeyId, revision, schema));
}

export async function encryptBundle({ storageKey, dataKeyId, revision, plaintext }) {
  const key = await hkdfSha256(storageKey, new Uint8Array(32), `acp/key-bundle/v1|${dataKeyId}`);
  const nonce = randomBytes(12);
  const aad = text.encode(`acp/key-bundle/v1|${dataKeyId}|${revision}`);
  return { nonce, ciphertext: await aesGcmEncrypt(key, nonce, plaintext, aad) };
}

export async function decryptBundle({ storageKey, dataKeyId, revision, nonce, ciphertext }) {
  const key = await hkdfSha256(storageKey, new Uint8Array(32), `acp/key-bundle/v1|${dataKeyId}`);
  const aad = text.encode(`acp/key-bundle/v1|${dataKeyId}|${revision}`);
  return aesGcmDecrypt(key, nonce, ciphertext, aad);
}

export function ownerCopyAad(shareId, version) {
  return text.encode(`acp/share-owner-copy/v1|${shareId}|${version}`);
}

export async function encryptOwnerCopy({ storageKey, dataKeyId, shareId, version, plaintext }) {
  const key = await hkdfSha256(storageKey, new Uint8Array(32), `acp/share-owner-copy/v1|${shareId}|${version}`);
  const nonce = randomBytes(12);
  return { nonce, ciphertext: await aesGcmEncrypt(key, nonce, plaintext, ownerCopyAad(shareId, version)) };
}

export async function decryptOwnerCopy({ storageKey, dataKeyId, shareId, version, nonce, ciphertext }) {
  const key = await hkdfSha256(storageKey, new Uint8Array(32), `acp/share-owner-copy/v1|${shareId}|${version}`);
  return aesGcmDecrypt(key, nonce, ciphertext, ownerCopyAad(shareId, version));
}

export function hpkeAad({ purpose, contextId, ownerId, recipientId, keyId, version }) {
  return text.encode(`hpke-v1|${purpose}|${contextId}|${ownerId}|${recipientId}|${keyId}|${version}`);
}

export async function generateHpkeKeyPair() {
  const hpke = suite();
  const pair = await hpke.kem.generateKeyPair();
  return {
    publicKey: copyBytes(await hpke.kem.serializePublicKey(pair.publicKey)),
    privateKey: copyBytes(await hpke.kem.serializePrivateKey(pair.privateKey)),
  };
}

export async function hpkeSeal(publicKeyBytes, plaintext, aadBytes) {
  const hpke = suite();
  const recipientPublicKey = await hpke.kem.deserializePublicKey(publicKeyBytes);
  const sealed = await hpke.seal({ recipientPublicKey, info: aadBytes }, plaintext, aadBytes);
  return { enc: copyBytes(sealed.enc), ciphertext: copyBytes(sealed.ct) };
}

export async function hpkeOpen(privateKeyBytes, enc, ciphertext, aadBytes) {
  const hpke = suite();
  const recipientKey = await hpke.kem.deserializePrivateKey(privateKeyBytes);
  const opened = await hpke.open({ recipientKey, enc, info: aadBytes }, ciphertext, aadBytes);
  return copyBytes(opened);
}

export function buildAnswerDocument(items) {
  return { schema: 1, items };
}

export function selectedShareItems(document, selectedIds) {
  const items = {};
  for (const id of selectedIds) {
    if (Object.prototype.hasOwnProperty.call(document.items, id)) items[id] = document.items[id];
  }
  return { schema: 1, items };
}

export function createShareUrl({ origin, shareId, privateKey, startToken }) {
  const url = new URL(`/view/${shareId}`, origin);
  const params = new URLSearchParams();
  params.set("k", bytesToB64url(privateKey));
  params.set("t", bytesToB64url(startToken));
  url.hash = params.toString();
  return url.toString();
}

export function parseShareFragment(hash) {
  const params = new URLSearchParams(String(hash || "").replace(/^#/, ""));
  const key = params.get("k");
  const token = params.get("t");
  if (!key || !token) return null;
  return { privateKey: b64urlToBytes(key), startToken: b64urlToBytes(token) };
}

const QR_SECRET = "wn-qr-view-secret";

export function holdQrSecrets(storage, shareId, secrets) {
  storage.setItem(QR_SECRET, JSON.stringify({
    shareId,
    k: bytesToB64url(secrets.privateKey),
    t: bytesToB64url(secrets.startToken),
    attempt: secrets.attempt,
  }));
}

export function readQrSecrets(storage, shareId) {
  const raw = storage.getItem(QR_SECRET);
  if (!raw) return null;
  const parsed = JSON.parse(raw);
  if (parsed.shareId !== shareId) return null;
  return {
    privateKey: b64urlToBytes(parsed.k),
    startToken: b64urlToBytes(parsed.t),
    attempt: parsed.attempt,
  };
}

export function clearQrSecrets(storage) {
  storage.removeItem(QR_SECRET);
}

export async function detectPrf() {
  if (typeof PublicKeyCredential === "undefined") {
    return {
      supported: false,
      reason: "このブラウザはパスキーに対応していません。対応しているブラウザで最初から登録してください。",
    };
  }
  if (typeof PublicKeyCredential.getClientCapabilities === "function") {
    try {
      const capabilities = await PublicKeyCredential.getClientCapabilities();
      if (capabilities && capabilities.prf === false) {
        return {
          supported: false,
          reason: "このブラウザは、回答を復号する鍵をパスキーで守る機能（PRF）に対応していません。別のブラウザまたは端末で登録してください。",
        };
      }
    } catch (_error) {
      /* 能力が読めない場合は、登録結果のPRF出力で判断する。 */
    }
  }
  return { supported: true, reason: "" };
}

export function canRegister(prf) {
  return prf.supported === true;
}
