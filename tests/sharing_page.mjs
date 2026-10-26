import assert from "node:assert/strict";
import { bindSharingPage } from "../app/static/js/watashi/sharing.mjs";

class Element {
  children = [];
  listeners = {};
  textContent = "";
  checked = false;
  disabled = false;
  hidden = false;
  value = "";
  classList = { toggle() {} };
  set innerHTML(_) { throw new Error("Answers must be rendered as text"); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name, event = {}) { return this.listeners[name](event); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  removeAttribute(name) { delete this[name]; }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    if (selector === "*") return descendants;
    if (selector === "button") return descendants.filter(child => child.tagName === "BUTTON");
    return descendants.filter(child => child.tagName === "INPUT" && (!selector.includes(":checked") || child.checked));
  }
}
const ids = ["sharing-page", "share-fields", "status", "unlock-here", "refresh-shares", "confirm-share",
  "create-qr", "qr-shares", "preview-output", "chapters", "questions", "expires-at", "expiry-field",
  "qr-output", "share-url", "open-link", "login-here", "lock-state", "show-preview", "link-facts", "copy-link"];
const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
const scope = { value: "questions" }, mode = { value: "emergency" };
const root = {
  querySelector(selector) {
    if (selector === "input[name=scope]:checked") return scope;
    if (selector === "input[name=mode]:checked") return mode;
    return nodes[selector.slice(1)];
  },
  createElement(tag) { const node = new Element(); node.tagName = tag.toUpperCase(); node.ownerDocument = root; return node; },
  createTextNode(text) { const node = new Element(); node.textContent = text; return node; },
};
for (const node of Object.values(nodes)) node.ownerDocument = root;
let userId = "owner";
let source = { schema: 1, items: { q1: { state: "answered", value: "<img src=x onerror=alert(1)>" }, q2: { state: "answered", value: "対象外の回答" } } };
let created = [];
let stopped = 0;
let failCreate = false;
let suspendLoad = null;
let finishLoad;
const client = {
  getUnlockState: () => ({ unlocked: true }),
  async unlock() {},
  async api(path) {
    if (path.endsWith("/auth/session")) return { authenticated: true, user_id: userId, display_name: "テスト" };
    if (path.endsWith("/answers/questions")) return { categories: [{ id: "c1", name: "章" }], questions: [
      { id: "q1", category_id: "c1", text: "質問1" }, { id: "q2", category_id: "c1", text: "質問2" },
    ] };
    if (path.endsWith("/stop")) { stopped++; return {}; }
    if (path.endsWith("/qr-shares")) return { shares: created.map((_, index) => ({ id: `share${index}`, status: "active", mode: "emergency", created_at: "2026-10-26" })) };
    throw new Error(`Unexpected API: ${path}`);
  },
  async loadAnswerDocument() {
    if (suspendLoad) await suspendLoad;
    return { revision: 1, document: structuredClone(source) };
  },
  async createQrShare(options) {
    if (failCreate) throw new Error("保存失敗のテスト");
    created.push(structuredClone(options));
    return { url: "http://localhost/view/test#fixture-only", share: { mode: options.mode, created_at: "2026-10-26" } };
  },
};
nodes["qr-output"].hidden = true;
await bindSharingPage(root, client);
assert.equal(nodes["share-fields"].disabled, false);
assert.equal(nodes["create-qr"].disabled, true);
// Empty selection never creates a share.
await nodes["show-preview"].fire("click");
assert.equal(nodes["confirm-share"].disabled, true);
assert.equal(created.length, 0);
const choices = nodes.questions.querySelectorAll("input");
choices[0].checked = true;
await nodes["show-preview"].fire("click");
const previewAnswers = nodes["preview-output"].children[1].children;
assert.equal(previewAnswers.length, 1);
assert.ok(previewAnswers[0].textContent.includes("<img src=x onerror=alert(1)>"));
// Expiry/mode changes invalidate confirmation, including when changed programmatically.
nodes["confirm-share"].checked = true;
nodes["share-fields"].fire("change", { target: nodes["confirm-share"] });
assert.equal(nodes["create-qr"].disabled, false);
mode.value = "timed";
nodes["expires-at"].value = new Date(Date.now() + 3600000).toISOString();
await nodes["create-qr"].fire("click");
assert.equal(created.length, 0);
assert.equal(nodes["confirm-share"].checked, false);
mode.value = "emergency";
await nodes["show-preview"].fire("click");
nodes["confirm-share"].checked = true;
failCreate = true;
await nodes["create-qr"].fire("click");
assert.equal(nodes["qr-output"].hidden, true);
assert.ok(nodes.status.textContent.includes("保存失敗"));
// A later answer edit cannot replace the content the sender confirmed.
failCreate = false;
source.items.q1.value = "確認後に更新された回答";
await nodes["create-qr"].fire("click");
assert.equal(created.length, 1);
assert.equal(created[0].payload.items.q1.value, "<img src=x onerror=alert(1)>");
assert.ok(!Object.hasOwn(created[0].payload.items, "q2"));
assert.equal(nodes["qr-output"].hidden, false);
assert.equal(nodes["confirm-share"].checked, false);
await nodes["qr-shares"].querySelectorAll("button")[0].fire("click");
assert.equal(stopped, 1);
// A late preview response cannot reinstate confirmation after settings change.
suspendLoad = new Promise(resolve => { finishLoad = resolve; });
const loading = nodes["show-preview"].fire("click");
await Promise.resolve(); await Promise.resolve();
nodes["share-fields"].fire("change", { target: choices[0] });
finishLoad();
await loading;
assert.equal(nodes["confirm-share"].disabled, true);
// Switching accounts removes secrets and locks this page.
userId = "other-owner";
await nodes["refresh-shares"].fire("click");
assert.equal(nodes["share-fields"].disabled, true);
assert.equal(nodes["share-url"].value, "");
assert.equal(nodes["qr-output"].hidden, true);
console.log("Sharing page interactions passed");
