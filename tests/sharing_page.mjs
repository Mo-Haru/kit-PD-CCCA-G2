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
  dataset = {};
  open = false;
  attributes = {};
  classList = { toggle() {} };
  set innerHTML(_) { throw new Error("Answers must be rendered as text"); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name, event = {}) { return this.listeners[name](event); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  removeAttribute(name) { delete this[name]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    if (selector === "*") return descendants;
    if (selector === "button") return descendants.filter(child => child.tagName === "BUTTON");
    return descendants.filter(child => child.tagName === "INPUT" && (!selector.includes(":checked") || child.checked));
  }
}
const ids = ["sharing-page", "share-fields", "status", "unlock-here", "refresh-shares", "confirm-share",
  "create-qr", "qr-shares", "preview-output", "chapters", "questions", "expires-at", "expiry-field",
  "qr-output", "share-url", "open-link", "login-here", "lock-state", "show-preview", "link-facts", "copy-link",
  "sharing-unlock-help", "encrypted-answers", "answer-ciphertext", "all-answers", "share-detail",
  "share-stop-top", "share-stop-bottom", "share-close-top", "share-close-bottom", "share-detail-status",
  "share-detail-facts", "share-detail-unlock", "share-detail-cipher", "share-detail-answers", "long-warning"];
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
let confirmStop = false;
const stopPrompts = [];
globalThis.confirm = message => { stopPrompts.push(message); return confirmStop; };
let failCreate = false;
let unlocked = false;
let unlockFailure = false;
let decryptFailure = false;
let reauthFailure = false;
const issued = [];
let suspendLoad = null;
let finishLoad;
const client = {
  getUnlockState: () => ({ unlocked }),
  async unlock() {
    if (unlockFailure) throw Object.assign(new Error("cancel"), { name: "NotAllowedError" });
    unlocked = true;
  },
  async api(path) {
    if (path.endsWith("/auth/session")) return { authenticated: true, user_id: userId, display_name: "テスト" };
    if (path.endsWith("/answers/questions")) return { categories: [{ id: "c1", name: "章" }], questions: [
      { id: "q1", category_id: "c1", text: "質問1" }, { id: "q2", category_id: "c1", text: "質問2" },
    ] };
    if (path.endsWith("/answers/current")) return { revision: 1, document: { ciphertext: "Encrypted-document-fixture-ciphertext" } };
    if (path.endsWith("/stop")) {
      stopped++;
      issued.find(share => path.includes(`/${share.id}/`)).status = "stopped";
      return {};
    }
    if (path.endsWith("/qr-shares")) return { shares: issued };
    if (path.includes("/qr-shares/")) return { share: issued.find(share => path.endsWith(`/${share.id}`)) };
    throw new Error(`Unexpected API: ${path}`);
  },
  async loadAnswerDocument() {
    if (decryptFailure) throw new Error("復号失敗のテスト");
    if (suspendLoad) await suspendLoad;
    return { revision: 1, document: structuredClone(source) };
  },
  async createQrShare(options) {
    if (failCreate) throw new Error("保存失敗のテスト");
    if (reauthFailure) throw Object.assign(new Error("再認証"), { code: "reauth_required" });
    created.push(structuredClone(options));
    const share = { id: `share${created.length - 1}`, status: "active", mode: options.mode,
      created_at: "2026-10-26", current_version: 1, version: { ciphertext: "Shared-fixture-ciphertext" } };
    issued.push(share);
    return { url: "http://localhost/view/test#fixture-only", share };
  },
  async openOwnedQrShare(share) {
    if (share.id === "legacy") throw Object.assign(new Error("旧形式のため控えがありません"), { code: "owner_copy_unavailable" });
    return structuredClone(created[Number(share.id.replace("share", ""))].payload);
  },
};
const text = element => [element.textContent, ...element.children.map(text)].join("\n");
nodes["qr-output"].hidden = true;
await bindSharingPage(root, client);
assert.equal(nodes["share-fields"].disabled, false);
assert.equal(nodes["unlock-here"].dataset.unlockState, "locked");
assert.equal(nodes["unlock-here"].textContent, "回答の暗号化を解除");
assert.equal(nodes["show-preview"].disabled, true);
assert.ok(text(nodes.questions).includes("Encrypted-document"));
assert.ok(!text(nodes.questions).includes("<img"));
const choices = nodes.questions.querySelectorAll("input");
choices[0].checked = true;
unlockFailure = true;
await nodes["unlock-here"].fire("click");
assert.equal(nodes["unlock-here"].dataset.unlockState, "locked");
unlockFailure = false;
decryptFailure = true;
await nodes["unlock-here"].fire("click");
assert.equal(nodes["unlock-here"].dataset.unlockState, "locked");
assert.ok(!text(nodes.questions).includes("<img"));
decryptFailure = false;
await nodes["unlock-here"].fire("click");
assert.equal(nodes["unlock-here"].textContent, "解除済み");
assert.equal(nodes["unlock-here"].dataset.unlockState, "unlocked");
assert.equal(nodes["unlock-here"].disabled, true);
assert.ok(!text(nodes.questions).includes("Encrypted-document"));
assert.ok(text(nodes.questions).includes("<img src=x onerror=alert(1)>"));
assert.equal(choices[0].checked, true);
assert.equal(nodes["show-preview"].disabled, false);
assert.equal(nodes["create-qr"].disabled, true);
// Empty selection never creates a share.
choices[0].checked = false;
await nodes["share-fields"].fire("change", { target: choices[0] });
assert.equal(nodes["show-preview"].disabled, true);
await nodes["show-preview"].fire("click");
assert.equal(nodes["confirm-share"].disabled, true);
assert.equal(created.length, 0);
choices[0].checked = true;
await nodes["share-fields"].fire("change", { target: choices[0] });
assert.equal(nodes["show-preview"].disabled, false);
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
// Invalid expiry keeps preview unavailable until a valid future date is entered.
nodes["expires-at"].value = new Date(Date.now() - 3600000).toISOString();
await nodes["expires-at"].fire("input");
assert.equal(nodes["show-preview"].disabled, true);
nodes["expires-at"].value = new Date(Date.now() + 3600000).toISOString();
await nodes["expires-at"].fire("input");
assert.equal(nodes["show-preview"].disabled, false);
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
const opener = nodes["qr-shares"].querySelectorAll("button")[0];
assert.equal(opener.dataset.shareState, "active");
assert.equal(opener.children[1].textContent, "開く");
assert.ok(text(opener.children[0]).includes("発行 2026-10-26"));
assert.equal(opener.querySelectorAll("button").length, 0);
await opener.fire("click");
assert.equal(nodes["share-detail"].open, true);
assert.ok(text(nodes["share-detail-answers"]).includes("<img src=x onerror=alert(1)>"));
assert.ok(!text(nodes["share-detail-answers"]).includes("確認後に更新された回答"));
assert.equal(nodes["share-stop-top"].disabled, false);
assert.equal(nodes["share-stop-bottom"].disabled, false);
await nodes["share-stop-bottom"].fire("click");
assert.equal(stopped, 0);
assert.ok(stopPrompts[0].includes("この共有を停止しますか"));
assert.equal(nodes["share-stop-top"].disabled, false);
assert.equal(nodes["qr-shares"].querySelectorAll("button")[0].dataset.shareState, "active");
confirmStop = true;
await nodes["share-stop-bottom"].fire("click");
assert.equal(stopped, 1);
assert.equal(nodes["qr-shares"].querySelectorAll("button")[0].dataset.shareState, "stopped");
assert.equal(nodes["share-stop-top"].disabled, true);
assert.equal(nodes["share-stop-bottom"].textContent, "共有停止済み");
nodes["share-close-bottom"].fire("click");
assert.equal(nodes["share-detail"].open, false);
assert.equal(nodes["share-detail-answers"].children.length, 0);
assert.equal(opener.focused, true);
// Long links show a strong warning, default to three years, and never go past it.
assert.equal(nodes["long-warning"].hidden, true);
mode.value = "long";
nodes["share-fields"].fire("change", { target: mode });
assert.equal(nodes["long-warning"].hidden, false);
assert.equal(nodes["expiry-field"].hidden, false);
const longDefault = new Date(nodes["expires-at"].value);
const threeYears = new Date();
threeYears.setFullYear(threeYears.getFullYear() + 3);
assert.ok(Math.abs(longDefault - threeYears) < 120000, nodes["expires-at"].value);
const savedDefault = nodes["expires-at"].value;
nodes["expires-at"].value = new Date(Date.now() + 1100 * 86400000).toISOString();
await nodes["expires-at"].fire("input");
assert.equal(nodes["show-preview"].disabled, true);
nodes["expires-at"].value = savedDefault;
await nodes["expires-at"].fire("input");
assert.equal(nodes["show-preview"].disabled, false);
await nodes["show-preview"].fire("click");
assert.ok(text(nodes["preview-output"]).includes("長期（最長3年）"));
assert.ok(text(nodes["preview-output"]).includes("誰でも何回でも閲覧できます"));
nodes["confirm-share"].checked = true;
await nodes["create-qr"].fire("click");
assert.equal(created[1].mode, "long");
assert.equal(new Date(created[1].expiresAt).getTime(), longDefault.getTime());
await nodes["qr-shares"].querySelectorAll("button")[1].fire("click");
confirmStop = false;
await nodes["share-stop-top"].fire("click");
assert.equal(stopped, 1);
confirmStop = true;
await nodes["share-stop-top"].fire("click");
assert.equal(stopped, 2);
nodes["share-close-top"].fire("click");
assert.equal(nodes["share-detail"].open, false);
issued.push({ ...issued[0], id: "legacy", status: "expired" });
await nodes["refresh-shares"].fire("click");
assert.equal(nodes["qr-shares"].querySelectorAll("button")[2].dataset.shareState, "expired");
await nodes["qr-shares"].querySelectorAll("button")[2].fire("click");
assert.ok(nodes["share-detail-status"].textContent.includes("旧形式"));
let cancelled = false;
nodes["share-detail"].fire("cancel", { preventDefault() { cancelled = true; } });
assert.ok(cancelled);
assert.equal(nodes["share-detail"].open, false);
// A late preview response cannot reinstate confirmation after settings change.
suspendLoad = new Promise(resolve => { finishLoad = resolve; });
const loading = nodes["show-preview"].fire("click");
await Promise.resolve(); await Promise.resolve();
nodes["share-fields"].fire("change", { target: choices[0] });
finishLoad();
await loading;
assert.equal(nodes["confirm-share"].disabled, true);
suspendLoad = null;
await nodes["show-preview"].fire("click");
nodes["confirm-share"].checked = true;
reauthFailure = true;
await nodes["create-qr"].fire("click");
assert.equal(nodes["unlock-here"].dataset.unlockState, "locked");
assert.ok(!text(nodes.questions).includes("確認後に更新された回答"));
assert.equal(choices[0].checked, true);
reauthFailure = false;
await nodes["unlock-here"].fire("click");
assert.equal(nodes["unlock-here"].dataset.unlockState, "unlocked");
// Switching accounts removes secrets and locks this page.
userId = "other-owner";
await nodes["refresh-shares"].fire("click");
assert.equal(nodes["share-fields"].disabled, true);
assert.equal(nodes["share-url"].value, "");
assert.equal(nodes["qr-output"].hidden, true);
assert.ok(nodes["qr-shares"].querySelectorAll("button").every(button => button.disabled));
console.log("Sharing page interactions passed");
