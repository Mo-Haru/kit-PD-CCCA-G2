/**
 * 家族画面で、同じ相手の共有を現在の新しい鍵で確認してから更新する。
 * 実在のパスキーや個人情報は使わない。
 */
import { bytesToB64url } from "../app/static/js/watashi/bytes.mjs";
import { encryptBundle, encryptDocument, encryptOwnerCopy, generateHpkeKeyPair, hpkeAad, hpkeSeal, wrapStorageKey } from "../app/static/js/watashi/crypto.mjs";

const failures = [];
function check(name, condition) {
  if (!condition) failures.push(name);
}

function createDocument() {
  const byId = new Map();
  const doc = {
    cookie: "wn_csrf=token",
    body: null,
    createElement(tag) {
      return createNode(tag);
    },
    createTextNode(text) {
      const node = createNode("text");
      node.textContent = text;
      return node;
    },
    querySelector(selector) {
      return find(doc.body, selector, true);
    },
    querySelectorAll(selector) {
      return find(doc.body, selector, false);
    },
  };

  function createNode(tag) {
    const node = {
      tag,
      name: "",
      type: "",
      checked: false,
      children: [],
      dataset: {},
      open: false,
      setAttribute(name, value) { node[name] = value; },
      showModal() { node.open = true; },
      close() { node.open = false; for (const callback of node.listeners.close || []) callback(); },
      focus() { node.focused = true; },
      listeners: {},
      ownerDocument: doc,
      append(...kids) {
        for (const kid of kids) { node.children.push(kid); kid.parentNode = node; }
      },
      replaceChildren(...kids) {
        node.children = [];
        if (kids.length) node.append(...kids);
      },
      addEventListener(type, fn) {
        const list = node.listeners[type] || [];
        list.push(fn);
        node.listeners[type] = list;
      },
      async click() {
        for (const fn of node.listeners.click || []) await fn();
      },
      querySelector(selector) {
        return find(node, selector, true);
      },
      querySelectorAll(selector) {
        return find(node, selector, false);
      },
    };
    let id = "";
    Object.defineProperty(node, "id", {
      get() { return id; },
      set(value) {
        if (id) byId.delete(id);
        id = String(value || "");
        if (id) byId.set(id, node);
      },
    });
    let storedValue = "";
    Object.defineProperty(node, "value", {
      get() {
        if (storedValue) return storedValue;
        const first = node.children.find((child) => child.tag === "option");
        return first ? first.value : "";
      },
      set(next) { storedValue = String(next || ""); },
    });
    Object.defineProperty(node, "textContent", {
      get() { return node._text || ""; },
      set(value) { node._text = String(value); },
    });
    Object.defineProperty(node, "selectedOptions", {
      get() {
        const current = node.value;
        return node.children.filter((child) => child.tag === "option" && child.value === current);
      },
    });
    return node;
  }

  function walk(node, visit) {
    if (!node) return;
    visit(node);
    for (const child of node.children || []) walk(child, visit);
  }

  function find(root, selector, first) {
    if (selector.startsWith("#") && !selector.includes(" ")) {
      const found = byId.get(selector.slice(1)) || null;
      return first ? found : (found ? [found] : []);
    }
    const collected = [];
    if (selector === "input[name=scope]:checked" || selector === "input[name=scope]") {
      walk(root, (node) => {
        if (node.tag === "input" && node.name === "scope" && (node.checked || !selector.endsWith(":checked"))) collected.push(node);
      });
    } else {
      const nested = selector.match(/^(#.+) input:checked$/);
      if (nested) {
        const parent = byId.get(nested[1].slice(1));
        walk(parent, (node) => {
          if (node.tag === "input" && node.checked) collected.push(node);
        });
      }
    }
    return first ? (collected[0] || null) : collected;
  }

  const body = createNode("div");
  doc.body = body;
  const ids = [
    "status", "lock-state", "incoming", "connections", "shares", "received",
    "chapters", "questions", "preview-output", "history-view", "received-view",
    "invite-link", "connection", "expires-at", "confirm-share", "login-here",
    "unlock-here", "invite-open", "copy-invite", "accept-open", "show-preview", "share",
    "invite-qr", "invite-qr-output", "invite-output", "all-answers", "encrypted-answers", "answer-ciphertext",
    "family-detail", "family-detail-title", "family-detail-body", "family-detail-status", "family-detail-unlock",
    "family-close-top", "family-close-bottom", "family-stop-top", "family-stop-bottom",
    "confirmation-output", "confirmation-code", "confirmation-link", "confirmation-qr", "confirmation-expiry",
    "copy-confirmation",
  ];
  for (const id of ids) {
    const tag = id === "connection" ? "select" : id === "expires-at" || id === "confirm-share" ? "input" : "div";
    const node = createNode(tag);
    node.id = id;
    if (id === "confirm-share") {
      node.type = "checkbox";
    }
    body.append(node);
  }
  const questions = createNode("input");
  questions.type = "radio";
  questions.name = "scope";
  questions.value = "questions";
  questions.checked = true;
  body.append(questions);
  for (const scope of ["all", "chapters"]) {
    const input = createNode("input");
    input.type = "radio";
    input.name = "scope";
    input.value = scope;
    body.append(input);
  }
  return doc;
}

const document = createDocument();
globalThis.document = document;
globalThis.location = { origin: "http://localhost:5000", pathname: "/family", hash: "" };
globalThis.history = { replaceState() {} };

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
const recipientKey = await generateHpkeKeyPair();
const receiveKey = await generateHpkeKeyPair();
const ownPayload = { schema: 1, items: { keep: { state: "answered", value: "発行時のダミー回答" } } };
const receivedPayload = { schema: 1, items: { keep: { state: "answered", value: "家族から届いたダミー回答" } } };
const personal = await encryptDocument({ storageKey, dataKeyId, userId: "user-1", revision: 1, schema: 1,
  plaintext: new TextEncoder().encode(JSON.stringify({ schema: 1, items: { keep: { state: "answered", value: "本人の保存済みダミー回答" } } })) });
const ownerCopy = await encryptOwnerCopy({ storageKey, dataKeyId, shareId: "share-1", version: 1,
  plaintext: new TextEncoder().encode(JSON.stringify(ownPayload)) });
const keyBundle = await encryptBundle({ storageKey, dataKeyId, revision: 1,
  plaintext: new TextEncoder().encode(JSON.stringify({ v: 1, receive: {
    publicKey: bytesToB64url(receiveKey.publicKey), privateKey: bytesToB64url(receiveKey.privateKey),
  } })) });
const incomingAad = hpkeAad({ purpose: "family", contextId: "received-share-1", ownerId: "user-2",
  recipientId: "user-1", keyId: "own-receive-key", version: 1 });
const sealed = await hpkeSeal(receiveKey.publicKey, new TextEncoder().encode(JSON.stringify(receivedPayload)), incomingAad);
const receivedShare = { id: "received-share-1", owner_user_id: "user-2", owner_display_name: "家族テスト",
  recipient_key_id: "own-receive-key", status: "active", current_version: 1,
  current: { hpke_enc: bytesToB64url(sealed.enc), ciphertext: bytesToB64url(sealed.ciphertext) } };
const calls = [];
let activeKeyId = "new-key";
let ownStatus = "key_pending";
let invitationList = [];
let confirmStop = false;
const stopPrompts = [];
globalThis.confirm = message => { stopPrompts.push(message); return confirmStop; };
const catalog = {
  categories: [{ id: "c1", name: "章" }],
  questions: [{ id: "keep", category_id: "c1", category_name: "章", text: "残す質問" }],
};
const challenge = { challenge: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))), rpId: "localhost", allowCredentials: [] };

globalThis.fetch = async (path, options = {}) => {
  const method = options.method || "GET";
  const body = options.body ? JSON.parse(options.body) : null;
  calls.push({ path, method, body });
  const reply = (status, payload) => new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  if (path === "/api/v1/auth/unlock/options" && method === "POST") {
    return reply(200, {
      public_key: challenge,
      envelope: {
        prf_input: bytesToB64url(crypto.getRandomValues(new Uint8Array(32))),
        kdf_salt: bytesToB64url(salt),
        wrapped_key: bytesToB64url(wrapped),
        nonce: bytesToB64url(wrapNonce),
        data_key_id: dataKeyId,
      },
      key_bundle: { revision: 1, nonce: bytesToB64url(keyBundle.nonce), ciphertext: bytesToB64url(keyBundle.ciphertext) },
    });
  }
  if (path === "/api/v1/auth/unlock/verify") return reply(200, { ok: true });
  if (path === "/api/v1/auth/session") {
    return reply(200, { authenticated: true, user_id: "user-1", display_name: "利用者", reauthenticated: true });
  }
  if (path === "/api/v1/answers/questions") return reply(200, catalog);
  if (path === "/api/v1/answers/current") return reply(200, { revision: 1, document: {
    revision: 1, data_key_id: dataKeyId, schema_version: 1, nonce: bytesToB64url(personal.nonce), ciphertext: bytesToB64url(personal.ciphertext),
  } });
  if (path === "/api/v1/family/invitations" && method === "POST") return reply(200, {
    invite_path: "/family/invite", token: "q".repeat(43), invitation: { status: "pending" },
  });
  if (path === "/api/v1/family/invitations") return reply(200, { invitations: invitationList });
  if (path === "/api/v1/family/invitations/inv-out/confirm" && method === "POST") {
    return body.code === "ABCD-EFGH"
      ? reply(200, { connection_id: "conn-2", recipient_display_name: "申請した家族" })
      : reply(400, { error: "confirmation_mismatch", message: "確認コードが一致しません。あと4回間違えると、この申請は無効になります。" });
  }
  if (path === "/api/v1/family/invitations/inv-in/confirmation" && method === "POST") return reply(200, {
    invitation: { id: "inv-in", status: "awaiting_sender" },
    confirmation: { token: "t".repeat(43), code: "WXYZ-2345", confirm_path: "/family/confirm", expires_at: "2099-01-01T00:00:00Z" },
  });
  if (path === "/api/v1/family/connections") {
    return reply(200, {
      connections: [{ id: "conn-1", status: "active", other_user_id: "user-2", other_display_name: "家族" }],
    });
  }
  if (path === "/api/v1/family/shares" && method === "GET") {
    return reply(200, {
      shares: [{
        id: "share-1",
        status: ownStatus,
        recipient_user_id: "user-2",
        recipient_display_name: "家族",
        recipient_key_id: "old-key",
        connection_id: "conn-1",
        current_version: 1,
        expires_at: null,
        owner_copy: { version: 1, data_key_id: dataKeyId, nonce: bytesToB64url(ownerCopy.nonce), ciphertext: bytesToB64url(ownerCopy.ciphertext) },
      }, {
        id: "expired-share", status: "active", recipient_display_name: "家族",
        expires_at: "2000-01-01T00:00:00Z", current_version: 1,
      }],
    });
  }
  if (path === "/api/v1/family/received") return reply(200, { shares: [receivedShare] });
  if (path === "/api/v1/family/received/received-share-1") return reply(200, { share: receivedShare });
  if (path === "/api/v1/encryption-keys/user-1") return reply(200, {
    keys: [{ id: "own-receive-key", status: "active", public_key: bytesToB64url(receiveKey.publicKey) }],
  });
  if (path === "/api/v1/encryption-keys/user-2") {
    return reply(200, {
      keys: [{ id: activeKeyId, status: "active", public_key: bytesToB64url(recipientKey.publicKey) }],
    });
  }
  if (path === "/api/v1/family/shares/share-1/update" && method === "POST") {
    ownStatus = "active";
    return reply(200, { share: { id: "share-1", status: "active", current_version: 2 } });
  }
  if (path === "/api/v1/family/shares/share-1/stop" && method === "POST") {
    ownStatus = "stopped";
    return reply(200, { ok: true, status: ownStatus });
  }
  return reply(500, { error: "unexpected", message: `${method} ${path}` });
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    credentials: {
      async get() {
        return {
          id: credentialId,
          rawId: bytesToB64url(crypto.getRandomValues(new Uint8Array(16))) && undefined,
          type: "public-key",
          response: {
            clientDataJSON: new Uint8Array([1]),
            authenticatorData: new Uint8Array([2]),
            signature: new Uint8Array([3]),
            userHandle,
          },
          getClientExtensionResults() {
            return { prf: { results: { first: prf } } };
          },
        };
      },
    },
  },
});
globalThis.navigator.credentials.get = async () => ({
  id: credentialId,
  rawId: new Uint8Array(credentialId.length),
  type: "public-key",
  response: {
    clientDataJSON: new Uint8Array([1]),
    authenticatorData: new Uint8Array([2]),
    signature: new Uint8Array([3]),
    userHandle,
  },
  getClientExtensionResults() {
    return { prf: { results: { first: prf } } };
  },
});

const { unlock } = await import("../app/static/js/watashi/client.mjs");
await unlock();
await import("../app/static/js/watashi/family.mjs");

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(document.querySelector("#status").textContent || "画面の準備が終わりません。");
}

function textOf(node) {
  if (!node) return "";
  let text = node.textContent || "";
  for (const child of node.children || []) text += textOf(child);
  return text;
}

function buttonNamed(label) {
  const found = [];
  const visit = (node) => {
    if (node.tag === "button" && node.textContent === label) found.push(node);
    for (const child of node.children || []) visit(child);
  };
  visit(document.body);
  return found[0] || null;
}

await waitFor(() => document.querySelector("#shares").children.length > 0 && document.querySelector("#questions").children.length > 0);
check("preview is unavailable with no selected question", document.querySelector("#show-preview").disabled);
check("sharing is unavailable before content confirmation", document.querySelector("#share").disabled);
check("owner card has a clear open action", document.querySelector("#shares").children[0].children[0].children[1].textContent === "開く");
check("pending key is visually distinct from active sharing", document.querySelector("#shares").children[0].children[0].dataset.shareState === "key_pending");
check("expired share is not shown as active", document.querySelector("#shares").children[1].children[0].dataset.shareState === "expired" && textOf(document.querySelector("#shares").children[1]).includes("期限切れ"));
await document.querySelector("#shares").children[0].children[0].click();
check("owner share opens in modal", document.querySelector("#family-detail").open);
check("owner modal shows the issued snapshot", textOf(document.querySelector("#family-detail-body")).includes("発行時のダミー回答"));
check("owner modal does not substitute the latest answer", !textOf(document.querySelector("#family-detail-body")).includes("本人の保存済みダミー回答"));
check("pending share asks to confirm the current key", textOf(document.querySelector("#family-detail-body")).includes("確認した現在の鍵で更新してください"));
const expiryInput = document.querySelector("#share-expiry-share-1");
check("expiry input starts hidden", expiryInput.parentNode.parentNode.hidden);
await buttonNamed("期限を変更").click();
check("expiry change reveals input", !expiryInput.parentNode.parentNode.hidden);
await buttonNamed("期限を変更").click();
check("expiry input can be dismissed", expiryInput.parentNode.parentNode.hidden);
const question = document.querySelector("#questions").querySelectorAll("input:checked").length
  ? null
  : document.querySelector("#questions").children[0].children[0];
question.checked = true;
for (const callback of document.querySelector("#questions").listeners.change || []) callback();
check("preview is available with a selected question and family", !document.querySelector("#show-preview").disabled);
const recipient = document.querySelector("#connection");
const recipientOptions = [...recipient.children];
recipient.replaceChildren();
for (const callback of recipient.listeners.change || []) callback();
check("preview is unavailable without a family recipient", document.querySelector("#show-preview").disabled);
recipient.replaceChildren(...recipientOptions);
const shareExpiry = document.querySelector("#expires-at");
shareExpiry.value = new Date(Date.now() - 3600000).toISOString();
for (const callback of shareExpiry.listeners.input || []) callback();
check("preview is unavailable with an expired date", document.querySelector("#show-preview").disabled);
shareExpiry.value = "";
for (const callback of shareExpiry.listeners.input || []) callback();
check("preview becomes available after clearing expiry", !document.querySelector("#show-preview").disabled);
await document.querySelector("#show-preview").click();
check("sharing stays unavailable until confirmation is checked", document.querySelector("#share").disabled);
check("preview shows the new key", textOf(document.querySelector("#preview-output")).includes("鍵：new-key"));
activeKeyId = "other-key";
document.querySelector("#confirm-share").checked = true;
const before = calls.filter((item) => item.path.endsWith("/update")).length;
await buttonNamed("この範囲で更新").click();
check("a key change after preview does not send the old key", calls.filter((item) => item.path.endsWith("/update")).length === before);
check("the page asks for confirmation again", document.querySelector("#status").textContent.includes("確認"));
activeKeyId = "new-key";
await document.querySelector("#show-preview").click();
document.querySelector("#confirm-share").checked = true;
await buttonNamed("この範囲で更新").click();
const posted = calls.findLast((item) => item.path === "/api/v1/family/shares/share-1/update");
check("the confirmed new key is sent", posted && posted.body.recipient_key_id === "new-key");
check("the stored old key is not sent", posted && posted.body.recipient_key_id !== "old-key");
check("active owner card is marked for green styling", document.querySelector("#shares").children[0].children[0].dataset.shareState === "active");
await document.querySelector("#family-stop-top").click();
check("stopping asks for confirmation", stopPrompts[0]?.includes("この共有を停止しますか"));
check("cancel does not send a stop request", !calls.some(item => item.path.endsWith("/stop")));
check("cancel leaves the share active", ownStatus === "active" && !document.querySelector("#family-stop-bottom").disabled);
confirmStop = true;
await document.querySelector("#family-stop-bottom").click();
check("confirmed stop sends one request", calls.filter(item => item.path.endsWith("/stop")).length === 1);
check("stopped card is marked for muted styling", document.querySelector("#shares").children[0].children[0].dataset.shareState === "stopped");
check("both stop buttons are disabled after stopping", document.querySelector("#family-stop-top").disabled && document.querySelector("#family-stop-bottom").disabled);
await document.querySelector("#family-stop-top").click();
check("already stopped share cannot send another stop request", calls.filter(item => item.path.endsWith("/stop")).length === 1);
await document.querySelector("#family-close-bottom").click();
check("close button closes the dialog", !document.querySelector("#family-detail").open);
check("closed modal clears answers", document.querySelector("#family-detail-body").children.length === 0);
check("received card identifies family", textOf(document.querySelector("#received")).includes("家族テスト さんからの共有"));
check("received card has the same open action", document.querySelector("#received").children[0].children[0].children[1].textContent === "開く");
check("active received card is marked for green styling", document.querySelector("#received").children[0].children[0].dataset.shareState === "active");
await document.querySelector("#received").children[0].children[0].click();
check("received modal decrypts family answer", textOf(document.querySelector("#family-detail-body")).includes("家族から届いたダミー回答"));
check("received modal has no sender-only stop action", document.querySelector("#family-stop-top").hidden && document.querySelector("#family-stop-bottom").hidden);
await document.querySelector("#family-close-top").click();
const scopes = document.querySelectorAll("input[name=scope]");
for (const scope of ["all", "chapters", "questions"]) {
  for (const input of scopes) input.checked = input.value === scope;
  for (const callback of scopes[0].listeners.change || []) callback();
  check(`scope ${scope} controls question visibility`, document.querySelector("#questions").hidden === (scope !== "questions"));
  check(`scope ${scope} controls chapter visibility`, document.querySelector("#chapters").hidden === (scope !== "chapters"));
  check(`scope ${scope} controls all-answer visibility`, document.querySelector("#all-answers").hidden === (scope !== "all"));
}
await document.querySelector("#invite-qr").click();
const image = document.querySelector("#invite-qr-output").children[0];
check("invite QR is generated locally", image && image.src.startsWith("data:image/gif;base64,"));
const inviteUrl = new URL(document.querySelector("#invite-link").textContent);
check("invitation token stays out of query parameters", inviteUrl.pathname === "/family/invite" && inviteUrl.search === "" && inviteUrl.hash.includes("token="));
check("QR opens the same dedicated invitation page", document.querySelector("#invite-output").hidden === false);

// 申請が届いても、表示名だけでは追加できない。申請者本人から受け取ったコードで追加する。
invitationList = [
  { id: "inv-out", kind: "open", status: "awaiting_sender", incoming: false, recipient_display_name: "申請した家族" },
  { id: "inv-in", kind: "open", status: "awaiting_sender", incoming: true, sender_display_name: "招待した家族" },
];
await document.querySelector("#invite-open").click();
check("no code-less add button remains", buttonNamed("家族に追加") === null);
const codeInput = document.querySelector("#confirm-code-inv-out");
check("sender sees a code field", codeInput && codeInput.tag === "input");
const confirmsBefore = calls.filter(item => item.path.endsWith("/inv-out/confirm")).length;
await buttonNamed("確認コードで家族に追加").click();
check("empty code is not sent", calls.filter(item => item.path.endsWith("/inv-out/confirm")).length === confirmsBefore);
codeInput.value = "AAAA-AAAA";
await buttonNamed("確認コードで家族に追加").click();
check("wrong code shows remaining attempts", document.querySelector("#status").textContent.includes("あと4回"));
document.querySelector("#confirm-code-inv-out").value = "ABCD-EFGH";
await buttonNamed("確認コードで家族に追加").click();
const confirmPost = calls.findLast(item => item.path.endsWith("/inv-out/confirm"));
check("the typed code is sent", confirmPost && confirmPost.body.code === "ABCD-EFGH");
check("success names the added family", document.querySelector("#status").textContent.includes("申請した家族 さんを家族に追加しました"));
check("requester does not get a code field", document.querySelector("#confirm-code-inv-in") === null);
confirmStop = false;
await buttonNamed("確認コードを再発行").click();
check("reissue asks before invalidating the old code", !calls.some(item => item.path.endsWith("/inv-in/confirmation")));
confirmStop = true;
await buttonNamed("確認コードを再発行").click();
check("reissued code is shown", document.querySelector("#confirmation-code").textContent === "WXYZ-2345");
const handed = new URL(document.querySelector("#confirmation-link").textContent);
check("reissued link keeps the token in the fragment", handed.pathname === "/family/confirm" && handed.search === "" && handed.hash === `#token=${"t".repeat(43)}`);
check("reissued link has a local QR", document.querySelector("#confirmation-qr").src?.startsWith("data:image/gif;base64,"));
check("confirmation panel is visible", document.querySelector("#confirmation-output").hidden === false);

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("ok");
