import { readFileSync } from "node:fs";
import { b64urlToBytes, bytesToB64url } from "../app/static/js/watashi/bytes.mjs";
import {
  canRegister,
  createShareUrl,
  detectPrf,
  encryptDocument,
  decryptDocument,
  generateHpkeKeyPair,
  holdQrSecrets,
  hpkeAad,
  hpkeOpen,
  hpkeSeal,
  parseShareFragment,
  clearQrSecrets,
  selectedShareItems,
  unwrapStorageKey,
  wrapStorageKey,
} from "../app/static/js/watashi/crypto.mjs";

const failures = [];
function check(name, condition) {
  if (!condition) failures.push(name);
}

const prf = await detectPrf();
check("prf unsupported without authenticator", prf.supported === false && canRegister(prf) === false);

const document = {
  schema: 1,
  items: {
    keep: { state: "answered", value: "dummy" },
    hidden: { state: "declined" },
  },
};
const selected = selectedShareItems(document, ["keep"]);
check("unselected item omitted", !Object.hasOwn(selected.items, "hidden") && selected.items.keep.state === "answered");

const url = createShareUrl({
  origin: "http://localhost",
  shareId: "share-id",
  privateKey: new Uint8Array(32).fill(7),
  startToken: new Uint8Array(32).fill(9),
});
const parsed = new URL(url);
check("secret stays in fragment", parsed.pathname === "/view/share-id" && parsed.search === "" && parsed.hash.includes("k=") && parsed.hash.includes("t="));
check("fragment parses", parseShareFragment(parsed.hash).privateKey[0] === 7);

const storage = {
  value: null,
  setItem(_key, item) { this.value = item; },
  getItem() { return this.value; },
  removeItem() { this.value = null; },
};
holdQrSecrets(storage, "share-id", { privateKey: new Uint8Array(32), startToken: new Uint8Array(32), attempt: "attempt" });
check("secret held outside the url", storage.value.includes("share-id") && !url.includes(storage.value));
clearQrSecrets(storage);
check("secret cleared", storage.value === null);

const prfOutput = crypto.getRandomValues(new Uint8Array(32));
const salt = crypto.getRandomValues(new Uint8Array(32));
const storageKey = crypto.getRandomValues(new Uint8Array(32));
const nonce = crypto.getRandomValues(new Uint8Array(12));
const wrapped = await wrapStorageKey({
  prfOutput, kdfSalt: salt, storageKey, nonce,
  userHandleB64: "handle", credentialIdB64: "credential", dataKeyId: "data-key",
});
const openedKey = await unwrapStorageKey({
  prfOutput, kdfSalt: salt, wrappedKey: wrapped, nonce,
  userHandleB64: "handle", credentialIdB64: "credential", dataKeyId: "data-key",
});
check("wrap roundtrip", bytesToB64url(openedKey) === bytesToB64url(storageKey));

const packed = await encryptDocument({
  storageKey, dataKeyId: "data-key", userId: "user", revision: 2, schema: 1,
  plaintext: new TextEncoder().encode(JSON.stringify(document)),
});
const openedDocument = JSON.parse(new TextDecoder().decode(await decryptDocument({
  storageKey, dataKeyId: "data-key", userId: "user", revision: 2, schema: 1,
  nonce: packed.nonce, ciphertext: packed.ciphertext,
})));
check("document roundtrip", openedDocument.items.hidden.state === "declined");

const pair = await generateHpkeKeyPair();
const aad = hpkeAad({
  purpose: "family", contextId: "share", ownerId: "owner", recipientId: "recipient", keyId: "key", version: 1,
});
const sealed = await hpkeSeal(pair.publicKey, new TextEncoder().encode("selected-only"), aad);
const opened = new TextDecoder().decode(await hpkeOpen(pair.privateKey, sealed.enc, sealed.ciphertext, aad));
check("hpke roundtrip", opened === "selected-only");
let tamperFailed = false;
try {
  const broken = new Uint8Array(sealed.ciphertext);
  broken[0] ^= 1;
  await hpkeOpen(pair.privateKey, sealed.enc, broken, aad);
} catch (_error) {
  tamperFailed = true;
}
check("hpke tamper detected", tamperFailed);

if (process.argv[2]) {
  const incoming = JSON.parse(readFileSync(process.argv[2], "utf8"));
  const fromPython = new TextDecoder().decode(await hpkeOpen(
    b64urlToBytes(incoming.privateKey),
    b64urlToBytes(incoming.enc),
    b64urlToBytes(incoming.ciphertext),
    b64urlToBytes(incoming.aad),
  ));
  check("python ciphertext opens in browser", fromPython === incoming.plaintext);
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(JSON.stringify({
  publicKey: bytesToB64url(pair.publicKey),
  privateKey: bytesToB64url(pair.privateKey),
  enc: bytesToB64url(sealed.enc),
  ciphertext: bytesToB64url(sealed.ciphertext),
  aad: bytesToB64url(aad),
  plaintext: "selected-only",
}));
