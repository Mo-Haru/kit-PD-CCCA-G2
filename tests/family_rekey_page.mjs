/**
 * 家族画面で、同じ相手の共有を現在の新しい鍵で確認してから更新する。
 * 実在のパスキーや個人情報は使わない。
 */
import { bytesToB64url } from "../app/static/js/watashi/bytes.mjs";
import { generateHpkeKeyPair, wrapStorageKey } from "../app/static/js/watashi/crypto.mjs";

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
      listeners: {},
      ownerDocument: doc,
      append(...kids) {
        for (const kid of kids) node.children.push(kid);
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
    if (selector === "input[name=scope]:checked") {
      walk(root, (node) => {
        if (node.tag === "input" && node.name === "scope" && node.checked) collected.push(node);
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
const calls = [];
let activeKeyId = "new-key";
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
      key_bundle: null,
    });
  }
  if (path === "/api/v1/auth/unlock/verify") return reply(200, { ok: true });
  if (path === "/api/v1/auth/session") {
    return reply(200, { authenticated: true, user_id: "user-1", display_name: "利用者", reauthenticated: true });
  }
  if (path === "/api/v1/answers/questions") return reply(200, catalog);
  if (path === "/api/v1/answers/current") return reply(200, { revision: 0, document: null });
  if (path === "/api/v1/family/invitations") return reply(200, { invitations: [] });
  if (path === "/api/v1/family/connections") {
    return reply(200, {
      connections: [{ id: "conn-1", status: "active", other_user_id: "user-2", other_display_name: "家族" }],
    });
  }
  if (path === "/api/v1/family/shares" && method === "GET") {
    return reply(200, {
      shares: [{
        id: "share-1",
        status: "key_pending",
        recipient_user_id: "user-2",
        recipient_display_name: "家族",
        recipient_key_id: "old-key",
        connection_id: "conn-1",
        current_version: 1,
        expires_at: null,
        owner_copy: null,
      }],
    });
  }
  if (path === "/api/v1/family/received") return reply(200, { shares: [] });
  if (path === "/api/v1/encryption-keys/user-2") {
    return reply(200, {
      keys: [{ id: activeKeyId, status: "active", public_key: bytesToB64url(recipientKey.publicKey) }],
    });
  }
  if (path === "/api/v1/family/shares/share-1/update" && method === "POST") {
    return reply(200, { share: { id: "share-1", status: "active", current_version: 2 } });
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

await waitFor(() => buttonNamed("この範囲で更新") && document.querySelector("#questions").children.length > 0);
check("pending share asks to confirm the current key", textOf(document.querySelector("#shares")).includes("確認した現在の鍵で更新してください"));
const question = document.querySelector("#questions").querySelectorAll("input:checked").length
  ? null
  : document.querySelector("#questions").children[0].children[0];
question.checked = true;
await document.querySelector("#show-preview").click();
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

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("ok");
