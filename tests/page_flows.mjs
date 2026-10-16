/**
 * 画面をまたいだ解錠、共有範囲、revision、QR開始、移行確認。
 * 実在のパスキーや個人情報は使わない。ブラウザのDOMは最小の代用品。
 */
import { b64urlToBytes, bytesToB64url } from "../app/static/js/watashi/bytes.mjs";
import {
  encryptBundle,
  encryptDocument,
  entriesFromPayload,
  expandShareSelection,
  fillAnswerList,
  generateHpkeKeyPair,
  hpkeAad,
  hpkeOpen,
  hpkeSeal,
  plaintextForSelection,
  presentAnswer,
  shareFingerprint,
  wrapStorageKey,
} from "../app/static/js/watashi/crypto.mjs";

const failures = [];
function check(name, condition) {
  if (!condition) failures.push(name);
}

function element() {
  const node = {
    children: [],
    text: "",
    html: null,
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); },
  };
  Object.defineProperty(node, "textContent", {
    get() { return node.text; },
    set(value) { node.text = String(value); node.html = null; },
  });
  Object.defineProperty(node, "innerHTML", {
    get() { return node.html; },
    set(value) { node.html = String(value); },
  });
  node.ownerDocument = { createElement: () => element() };
  return node;
}

const catalog = {
  categories: [
    { id: "c1", name: "章1" },
    { id: "c2", name: "章2" },
  ],
  questions: [
    { id: "keep", category_id: "c1", category_name: "章1", text: "残す質問" },
    { id: "later", category_id: "c1", category_name: "章1", text: "未定の質問" },
    { id: "skip", category_id: "c2", category_name: "章2", text: "隠す質問" },
    { id: "no", category_id: "c2", category_name: "章2", text: "辞退する質問" },
    { id: "blank", category_id: "c1", category_name: "章1", text: "まだ無い質問" },
  ],
};

const stored = {
  schema: 1,
  items: {
    keep: { state: "answered", value: "<script>alert(1)</script>" },
    later: { state: "undecided" },
    skip: { state: "answered", value: ["隠す", { secret: true }] },
    no: { state: "declined" },
  },
};

function throws(fn) {
  try {
    fn();
    return false;
  } catch (_error) {
    return true;
  }
}

check("all expands every question", expandShareSelection(catalog, { scope: "all" }).length === 5);
check("duplicate chapters collapse", expandShareSelection(catalog, { scope: "chapters", ids: ["c1", "c1"] }).join() === "keep,later,blank");
check("duplicate questions collapse", expandShareSelection(catalog, { scope: "questions", ids: ["no", "keep", "no"] }).join() === "no,keep");
check("missing selection is not everything", throws(() => expandShareSelection(catalog, undefined)));
check("empty selection is not everything", throws(() => expandShareSelection(catalog, { scope: "questions", ids: [] })));
check("unknown id is not everything", throws(() => expandShareSelection(catalog, { scope: "questions", ids: ["missing"] })));
check("all cannot carry ids", throws(() => expandShareSelection(catalog, { scope: "all", ids: ["keep"] })));

const chapterPayload = plaintextForSelection(stored, catalog, { scope: "chapters", ids: ["c1"] });
check("chapter omits the other chapter", !Object.hasOwn(chapterPayload.items, "skip") && !Object.hasOwn(chapterPayload.items, "no"));
check("unanswered stays explicit", chapterPayload.items.blank.state === "unanswered");
check("declined is kept when selected", plaintextForSelection(stored, catalog, { scope: "questions", ids: ["no"] }).items.no.state === "declined");
check("array values survive", presentAnswer(stored.items.skip).valueText.includes("隠す"));

const parent = element();
fillAnswerList(parent, entriesFromPayload(chapterPayload, catalog));
const shown = parent.children.map((row) => row.textContent).join("\n");
check("family view shows the answer", shown.includes("残す質問") && shown.includes("<script>alert(1)</script>"));
check("family view shows undecided and unanswered", shown.includes("未定") && shown.includes("未回答"));
check("markup is not assigned as html", parent.children.every((row) => row.html === null) && !shown.includes("隠す"));

function memoryStorage() {
  const data = new Map();
  return {
    setItem(key, value) { data.set(key, String(value)); },
    getItem(key) { return data.has(key) ? data.get(key) : null; },
    removeItem(key) { data.delete(key); },
  };
}

globalThis.document = {
  cookie: "wn_csrf=token",
  querySelector() { return null; },
};
globalThis.location = { origin: "http://localhost:5000" };
globalThis.PublicKeyCredential = {
  async getClientCapabilities() { return { prf: true }; },
};

const {
  captureQrSecrets, describeViewError, loadQrPage, openSharedPayload, replaceAttempt, resumeOrStartQr, viewFacts,
} = await import("../app/static/js/watashi/view.mjs");
const {
  addPasskey, confirmMigrationAccess, createFamilyShare, createQrShare, loadAnswerDocument, saveAnswerDocument,
  unlock, updateFamilyShare, updateQrShare,
} = await import("../app/static/js/watashi/client.mjs");

const shareId = "a".repeat(43);
const storage = memoryStorage();
const qrCalls = [];
let qrMode = "missing";
const qrApi = async (path, options = {}) => {
  const method = options.method || "GET";
  qrCalls.push({ path, method, body: options.body || null });
  if (method === "GET" && path === "/api/v1/qr-view/current") {
    if (qrMode === "missing" || qrMode === "expired") {
      const error = new Error("閲覧セッションがありません。");
      error.status = 401;
      throw error;
    }
    if (qrMode === "other") return { id: "other-share" };
    return { id: shareId };
  }
  if (method === "POST" && path === `/api/v1/qr-shares/${shareId}/start`) {
    qrMode = "ready";
    return { session_id: "session-1" };
  }
  throw new Error(`unexpected qr ${method} ${path}`);
};
const privateKey = crypto.getRandomValues(new Uint8Array(32));
const startToken = crypto.getRandomValues(new Uint8Array(32));
const hash = `#k=${bytesToB64url(privateKey)}&t=${bytesToB64url(startToken)}`;
const locationLike = { hash, replaceHash() { this.hash = ""; } };
const secrets = captureQrSecrets(storage, locationLike, shareId);
check("fragment leaves the address", locationLike.hash === "" && secrets.startToken[0] === startToken[0]);
const waiting = await loadQrPage({ api: qrApi, storage, shareId, secrets });
check("opening the page does not start", waiting.action === "wait" && qrCalls.length === 0);
const started = await resumeOrStartQr({ api: qrApi, shareId, secrets, resumeOnly: false });
check("explicit open starts once", started.started.session_id === "session-1" && qrCalls.filter((item) => item.method === "POST").length === 1);
storage.setItem("wn-qr-opened", shareId);
qrCalls.length = 0;
const resumed = await loadQrPage({ api: qrApi, storage, shareId, secrets });
check("reload resumes without a new start", resumed.action === "resume" && qrCalls.every((item) => item.method === "GET"));
qrMode = "expired";
qrCalls.length = 0;
const expired = await loadQrPage({ api: qrApi, storage, shareId, secrets });
check("expired reload does not start again", expired.action === "wait" && qrCalls.every((item) => item.method === "GET"));
qrMode = "other";
qrCalls.length = 0;
const wrong = await loadQrPage({ api: qrApi, storage, shareId, secrets });
check("another share is not reused", wrong.action === "wait" && wrong.error.code === "wrong_share" && qrCalls.every((item) => item.method === "GET"));
const replaced = replaceAttempt(storage, shareId);
check("a new attempt is distinct", replaced.attempt !== secrets.attempt);
const facts = viewFacts({ status: "used", updated_at: "2026-10-16T00:00:00Z" });
check("view separates validity and update time", facts.validity === "有効状態: 使用済み" && facts.updated === "共有の更新日時: 2026-10-16T00:00:00Z");
check("used, expired, and stopped stay distinct", describeViewError({ code: "qr_used" }).includes("使用済み")
  && describeViewError({ code: "qr_expired" }).includes("期限が切れ")
  && describeViewError({ code: "qr_stopped" }).includes("停止"));
let badCipher = false;
try {
  await openSharedPayload(privateKey, {
    owner_user_id: "owner",
    public_key: "not-the-key",
    current_version: 1,
    version: {
      hpke_enc: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))),
      ciphertext: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))),
    },
  }, shareId);
} catch (error) {
  badCipher = error.code === "decrypt_failed" && describeViewError(error).includes("復号に失敗");
}
check("bad ciphertext is a decrypt failure", badCipher);

const userHandle = crypto.getRandomValues(new Uint8Array(32));
const storageKey = crypto.getRandomValues(new Uint8Array(32));
const prf = crypto.getRandomValues(new Uint8Array(32));
const salt = crypto.getRandomValues(new Uint8Array(32));
const wrapNonce = crypto.getRandomValues(new Uint8Array(12));
const credentialId = bytesToB64url(crypto.getRandomValues(new Uint8Array(16)));
const dataKeyId = "data-key-1";
const wrapped = await wrapStorageKey({
  prfOutput: prf,
  kdfSalt: salt,
  storageKey,
  nonce: wrapNonce,
  userHandleB64: bytesToB64url(userHandle),
  credentialIdB64: credentialId,
  dataKeyId,
});
const receive = await generateHpkeKeyPair();
const bundleObject = {
  v: 1,
  receive: {
    publicKey: bytesToB64url(receive.publicKey),
    privateKey: bytesToB64url(receive.privateKey),
    keyId: "recv-1",
  },
};
const packedBundle = await encryptBundle({
  storageKey,
  dataKeyId,
  revision: 1,
  plaintext: new TextEncoder().encode(JSON.stringify(bundleObject)),
});

function assertion({ id, output }) {
  return {
    id,
    rawId: b64urlToBytes(id),
    type: "public-key",
    response: {
      clientDataJSON: new Uint8Array([1]),
      authenticatorData: new Uint8Array([2]),
      signature: new Uint8Array([3]),
      userHandle,
    },
    getClientExtensionResults() {
      return { prf: { results: { first: output } } };
    },
  };
}

function registration({ id, output }) {
  return {
    id,
    rawId: b64urlToBytes(id),
    type: "public-key",
    response: {
      clientDataJSON: new Uint8Array([1]),
      attestationObject: new Uint8Array([2]),
      getTransports() { return ["internal"]; },
    },
    getClientExtensionResults() {
      return { prf: { results: { first: output } } };
    },
  };
}

let nextAssertion = assertion({ id: credentialId, output: prf });
let nextRegistration = null;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    credentials: {
      async get() { return nextAssertion; },
      async create() { return nextRegistration; },
    },
  },
});

const log = [];
let saved = null;
let rejectStale = false;
let receivedShares = [];
let acknowledgements = 0;
let proofMode = "ok";
let storedPasskeyEnvelope = null;
const disabled = [];
const publicKey = { challenge: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))), rpId: "localhost", allowCredentials: [] };

globalThis.fetch = async (path, options = {}) => {
  const method = options.method || "GET";
  const body = options.body ? JSON.parse(options.body) : null;
  log.push({ path, method, body });
  const reply = (status, payload) => new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  if (path === "/api/v1/auth/unlock/options" && method === "POST") {
    return reply(200, {
      public_key: publicKey,
      envelope: {
        prf_input: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))),
        kdf_salt: bytesToB64url(salt),
        wrapped_key: bytesToB64url(wrapped),
        nonce: bytesToB64url(wrapNonce),
        data_key_id: dataKeyId,
      },
      key_bundle: {
        revision: 1,
        nonce: bytesToB64url(packedBundle.nonce),
        ciphertext: bytesToB64url(packedBundle.ciphertext),
      },
    });
  }
  if (path === "/api/v1/auth/session") {
    return reply(200, { authenticated: true, user_id: "user-1", display_name: "利用者", reauthenticated: true });
  }
  if (path === "/api/v1/encryption-keys/user-1") {
    return reply(200, { keys: [{ id: "recv-1", status: "active", public_key: bytesToB64url(receive.publicKey) }] });
  }
  if (path === "/api/v1/auth/unlock/verify") return reply(200, { ok: true });
  if (path === "/api/v1/answers/current" && method === "PUT") {
    if (rejectStale) return reply(409, { error: "revision_conflict", message: "別の更新が入ったため、保存できませんでした。" });
    saved = body;
    return reply(200, { revision: body.expected_revision + 1 });
  }
  if (path === "/api/v1/answers/current" && method === "GET") {
    if (!saved) return reply(200, { revision: 0, document: null });
    return reply(200, {
      revision: saved.expected_revision + 1,
      document: {
        data_key_id: saved.data_key_id,
        revision: saved.expected_revision + 1,
        nonce: saved.nonce,
        ciphertext: saved.ciphertext,
        crypto_version: 1,
        schema_version: 1,
      },
    });
  }
  if (path === "/api/v1/answers/questions") return reply(200, catalog);
  if (path === "/api/v1/qr-shares" && method === "POST") return reply(200, { share: { id: body.id, current_version: 1 } });
  if (method === "POST" && path.endsWith("/update") && path.includes("/qr-shares/")) {
    return reply(200, { share: { current_version: body.expected_version + 1 } });
  }
  if (path === "/api/v1/auth/passkeys/options") {
    return reply(200, {
      public_key: publicKey,
      prf_input: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))),
      user_handle: bytesToB64url(userHandle),
      data_key_id: dataKeyId,
    });
  }
  if (path === "/api/v1/auth/passkeys/verify") {
    storedPasskeyEnvelope = body.envelope;
    return reply(200, { passkey_id: `passkey-${disabled.length + 2}` });
  }
  if (path.endsWith("/proof/options")) {
    const envelope = { ...storedPasskeyEnvelope, data_key_id: dataKeyId };
    if (proofMode === "bad") envelope.wrapped_key = bytesToB64url(crypto.getRandomValues(new Uint8Array(48)));
    return reply(200, { public_key: publicKey, envelope });
  }
  if (path.endsWith("/proof/verify")) return reply(200, { ok: true });
  if (path.endsWith("/disable")) {
    disabled.push(path);
    return reply(200, { ok: true });
  }
  if (path === "/api/v1/family/shares" && method === "POST") return reply(200, { share: { id: body.id, current_version: 1 } });
  if (method === "POST" && /^\/api\/v1\/family\/shares\/[^/]+\/update$/.test(path)) {
    return reply(200, { share: { current_version: body.expected_version + 1 } });
  }
  if (path === "/api/v1/family/received") return reply(200, { shares: receivedShares });
  if (path.endsWith("/decrypt-confirmed")) {
    acknowledgements += 1;
    return reply(200, { ok: true, decrypt_confirmed: true });
  }
  return reply(500, { error: "unexpected", message: `${method} ${path}` });
};

let unlockFailed = false;
let acknowledgedEarly = false;
try {
  await confirmMigrationAccess("migration-early", {
    unlock: async () => { throw new Error("鍵を解除できません。"); },
    acknowledge: async () => { acknowledgedEarly = true; },
  });
} catch (_error) {
  unlockFailed = true;
}
check("failed unlock does not finish migration", unlockFailed && !acknowledgedEarly);

await unlock();
log.length = 0;
let revisionMissing = false;
try {
  await saveAnswerDocument(stored.items);
} catch (error) {
  revisionMissing = error.code === "revision_required";
}
check("omitted revision is not saved as latest", revisionMissing && log.length === 0);

await saveAnswerDocument(stored.items, 0);
check("save sends the editing revision only", log.length === 1 && log[0].method === "PUT" && log[0].body.expected_revision === 0);
const loaded = await loadAnswerDocument();
check("loaded revision roundtrips", loaded.revision === 1 && loaded.document.items.keep.value.includes("<script>"));
log.length = 0;
rejectStale = true;
let conflicted = false;
try {
  await saveAnswerDocument({ keep: { state: "answered", value: "古い内容" } }, 0);
} catch (error) {
  conflicted = error.status === 409 && error.code === "revision_conflict";
}
rejectStale = false;
const still = await loadAnswerDocument();
check("stale save leaves the first document", conflicted && still.document.items.keep.value.includes("<script>") && log[0].method === "PUT");

const qrLogAt = log.length;
let rejectedScope = false;
try {
  await createQrShare({ mode: "emergency" });
} catch (_error) {
  rejectedScope = true;
}
check("qr without a selection is not created", rejectedScope && log.slice(qrLogAt).every((item) => item.path !== "/api/v1/qr-shares"));

const created = await createQrShare({
  mode: "emergency",
  selection: { scope: "questions", ids: ["keep", "later", "blank", "keep"] },
});
const posted = log.findLast((item) => item.path === "/api/v1/qr-shares" && item.method === "POST").body;
const qrAad = hpkeAad({
  purpose: "qr", contextId: posted.id, ownerId: "user-1", recipientId: "qr", keyId: posted.public_key, version: 1,
});
const qrOpened = JSON.parse(new TextDecoder().decode(await hpkeOpen(
  created.privateKey, b64urlToBytes(posted.version.hpke_enc), b64urlToBytes(posted.version.ciphertext), qrAad,
)));
check("qr contains only the selected items", Object.keys(qrOpened.items).join() === "keep,later,blank");
check("qr keeps undecided and html text", qrOpened.items.later.state === "undecided" && qrOpened.items.keep.value.includes("<script>"));
check("qr marks a selected blank as unanswered", qrOpened.items.blank.state === "unanswered");

await updateQrShare({
  shareId: posted.id,
  expectedVersion: 1,
  publicKey: posted.public_key,
  selection: { scope: "questions", ids: ["no"] },
});
const updated = log.findLast((item) => item.path.endsWith("/update")).body;
const updatedAad = hpkeAad({
  purpose: "qr", contextId: posted.id, ownerId: "user-1", recipientId: "qr", keyId: posted.public_key, version: 2,
});
const updatedOpened = JSON.parse(new TextDecoder().decode(await hpkeOpen(
  created.privateKey, b64urlToBytes(updated.version.hpke_enc), b64urlToBytes(updated.version.ciphertext), updatedAad,
)));
check("qr update can shrink the selection", Object.keys(updatedOpened.items).join() === "no" && updatedOpened.items.no.state === "declined");

const familyShareId = "family-share-1";
const familyPayload = { schema: 1, items: { keep: { state: "answered", value: "共有した値" } } };
const familyAad = hpkeAad({
  purpose: "family", contextId: familyShareId, ownerId: "owner-9", recipientId: "user-1", keyId: "recv-1", version: 1,
});
const sealed = await hpkeSeal(receive.publicKey, new TextEncoder().encode(JSON.stringify(familyPayload)), familyAad);
receivedShares = [{
  id: familyShareId,
  owner_user_id: "owner-9",
  recipient_key_id: "recv-1",
  current_version: 1,
  current: { hpke_enc: bytesToB64url(sealed.enc), ciphertext: bytesToB64url(sealed.ciphertext) },
}];
const confirmed = await confirmMigrationAccess("migration-1");
check("migration decrypts existing records before acknowledgement", confirmed.answerCount === 4 && confirmed.shareCount === 1 && acknowledgements === 1);
receivedShares[0].current.ciphertext = bytesToB64url(crypto.getRandomValues(new Uint8Array(24)));
let decryptFailed = false;
try {
  await confirmMigrationAccess("migration-1");
} catch (_error) {
  decryptFailed = true;
}
check("broken share does not acknowledge again", decryptFailed && acknowledgements === 1);

const addedPrf = crypto.getRandomValues(new Uint8Array(32));
const addedId = bytesToB64url(crypto.getRandomValues(new Uint8Array(16)));
nextRegistration = registration({ id: addedId, output: addedPrf });
nextAssertion = assertion({ id: addedId, output: addedPrf });
proofMode = "ok";
const added = await addPasskey();
check("new passkey is kept only after proof", added.passkey_id && log.some((item) => item.path.endsWith("/proof/verify")) && disabled.length === 0);

const badPrf = crypto.getRandomValues(new Uint8Array(32));
const badId = bytesToB64url(crypto.getRandomValues(new Uint8Array(16)));
nextRegistration = registration({ id: badId, output: badPrf });
nextAssertion = assertion({ id: badId, output: badPrf });
proofMode = "bad";
let proofFailed = false;
try {
  await addPasskey();
} catch (_error) {
  proofFailed = true;
}
check("failed proof disables the new passkey", proofFailed && disabled.some((path) => path.includes("/disable")));
const afterFailure = await loadAnswerDocument();
check("failed proof leaves the current key usable", afterFailure.document.items.no.state === "declined");

const { confirmationStillMatches } = await import("../app/static/js/watashi/family.mjs");
const previewSelection = { scope: "questions", ids: ["keep"] };
const previewPayload = plaintextForSelection(stored, catalog, previewSelection);
const previewKey = shareFingerprint(previewSelection, previewPayload);
check("the previewed selection still matches", confirmationStillMatches(previewKey, previewSelection, previewPayload));
check("a later selection is not already confirmed", !confirmationStillMatches(previewKey, { scope: "questions", ids: ["no"] }, previewPayload));
check("changed answers are not already confirmed", !confirmationStillMatches(
  previewKey,
  previewSelection,
  plaintextForSelection({ schema: 1, items: { keep: { state: "answered", value: "別の値" } } }, catalog, previewSelection),
));
check("an empty confirmation matches nothing", !confirmationStillMatches("", previewSelection, previewPayload));

const confirmedPayload = plaintextForSelection(stored, catalog, { scope: "questions", ids: ["no"] });
const familyLog = log.length;
await createFamilyShare({
  connectionId: "conn-1",
  recipientUserId: "user-1",
  recipientKeyId: "recv-1",
  selection: { scope: "all" },
  payload: confirmedPayload,
});
const familyPosted = log.slice(familyLog).find((item) => item.path === "/api/v1/family/shares" && item.method === "POST");
const sealedFamily = JSON.parse(new TextDecoder().decode(await hpkeOpen(
  receive.privateKey,
  b64urlToBytes(familyPosted.body.version.hpke_enc),
  b64urlToBytes(familyPosted.body.version.ciphertext),
  hpkeAad({
    purpose: "family",
    contextId: familyPosted.body.id,
    ownerId: "user-1",
    recipientId: "user-1",
    keyId: "recv-1",
    version: 1,
  }),
)));
check("confirmed bytes are sealed without rereading the note", Object.keys(sealedFamily.items).join() === "no"
  && log.slice(familyLog).every((item) => item.path !== "/api/v1/answers/current"));
const updateLog = log.length;
await updateFamilyShare({
  shareId: familyPosted.body.id,
  expectedVersion: 1,
  recipientUserId: "user-1",
  recipientKeyId: "recv-1",
  selection: { scope: "all" },
  payload: confirmedPayload,
});
check("confirmed update does not reread the note", log.slice(updateLog).every((item) => item.path !== "/api/v1/answers/current"));

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("ok");
