import assert from "node:assert/strict";
import { bindSharingPage } from "../app/static/js/watashi/sharing.mjs";

class Element {
  children = [];
  listeners = {};
  textContent = "";
  className = "";
  checked = false;
  disabled = false;
  hidden = false;
  value = "";
  dataset = {};
  open = false;
  attributes = {};
  classList = { toggle() {} };
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name, event = {}) { return this.listeners[name](event); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    if (selector === "*") return descendants;
    if (selector === "button") return descendants.filter(child => child.tagName === "BUTTON");
    return descendants.filter(child => child.tagName === "INPUT");
  }
}
const ids = ["sharing-page", "status", "lock-state", "login-here", "share-start", "refresh-shares", "qr-shares",
  "share-detail", "share-stop", "share-close", "share-detail-status", "share-detail-facts", "share-detail-unlock",
  "share-detail-cipher", "share-detail-answers", "site-alert", "site-alert-text"];
const text = element => [element.textContent, ...element.children.map(text)].join("\n");

function setup({ authenticated = true, unlockFails = false } = {}) {
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  const root = {
    querySelector: selector => nodes[selector.slice(1)],
    createElement(tag) { const node = new Element(); node.tagName = tag.toUpperCase(); node.ownerDocument = root; return node; },
    createTextNode(text) { const node = new Element(); node.textContent = text; return node; },
  };
  for (const node of Object.values(nodes)) node.ownerDocument = root;
  const state = { unlocked: false, unlockCalls: 0, allowUnlock: !unlockFails, userId: "owner", stopped: 0, ownerCopies: 0,
    shares: [{ id: "share0", status: "active", mode: "emergency", created_at: "2026-11-06", expires_at: null,
      current_version: 1, version: { ciphertext: "Shared-fixture-ciphertext" } }] };
  const client = {
    getUnlockState: () => ({ unlocked: state.unlocked }),
    async unlock() {
      state.unlockCalls += 1;
      if (!state.allowUnlock) throw Object.assign(new Error("cancel"), { name: "NotAllowedError" });
      state.unlocked = true;
    },
    async api(path) {
      if (path.endsWith("/auth/session")) return authenticated ? { authenticated: true, user_id: state.userId, display_name: "テスト" } : { authenticated: false };
      if (path.endsWith("/answers/questions")) return { categories: [{ id: "c1", name: "章" }],
        questions: [{ id: "q1", category_id: "c1", text: "質問1" }] };
      if (path.endsWith("/stop")) {
        state.stopped += 1;
        state.shares.find(share => path.includes(`/${share.id}/`)).status = "stopped";
        return {};
      }
      if (path.endsWith("/qr-shares")) return { shares: state.shares };
      if (path.includes("/qr-shares/")) return { share: state.shares.find(share => path.endsWith(`/${share.id}`)) };
      throw new Error(`Unexpected API: ${path}`);
    },
    async openOwnedQrShare(share) {
      state.ownerCopies += 1;
      if (share.id === "legacy") throw Object.assign(new Error("旧形式のため控えがありません"), { code: "owner_copy_unavailable" });
      return { schema: 1, items: { q1: { state: "answered", value: "<img src=x onerror=alert(1)>" } } };
    },
  };
  return { nodes, state, bind: () => bindSharingPage(root, client) };
}
const navigations = [];
globalThis.location = { assign(path) { navigations.push(path); } };
let confirmStop = false;
const stopPrompts = [];
globalThis.confirm = message => { stopPrompts.push(message); return confirmStop; };

// --- 一覧と、共有を始めるボタン ---
{
  const { nodes, state, bind } = setup();
  await bind();
  assert.equal(nodes["share-start"].disabled, false);
  assert.equal(nodes["login-here"].hidden, true);
  assert.ok(nodes["lock-state"].textContent.includes("パスキーで本人確認"));
  await nodes["share-start"].fire("click");
  assert.equal(navigations.at(-1), "/sharing/new", "共有を作る手順は、別の画面で行う");
  const opener = nodes["qr-shares"].querySelectorAll("button")[0];
  assert.equal(opener.dataset.shareState, "active");
  assert.equal(opener.children[1].textContent, "開く");
  assert.ok(text(opener.children[0]).includes("発行 2026-11-06"));
  assert.equal(state.unlockCalls, 0, "開くまで、本人確認を求めない");

  // 開くと、その場でパスキーの本人確認を求める。取り消したら内容を出さず、続ける手段を出す。
  state.allowUnlock = false;
  await opener.fire("click");
  assert.equal(nodes["share-detail"].open, true);
  assert.equal(state.unlockCalls, 1);
  assert.equal(state.ownerCopies, 0, "取り消したら、回答を復号しない");
  assert.ok(nodes["share-detail-status"].textContent.includes("本人確認を完了できませんでした"));
  assert.equal(nodes["share-detail-unlock"].hidden, false);
  assert.equal(nodes["share-detail-cipher"].hidden, false);
  assert.equal(nodes["share-detail-answers"].children.length, 0);
  state.allowUnlock = true;
  await nodes["share-detail-unlock"].fire("click");
  assert.equal(state.unlockCalls, 2);
  assert.equal(nodes["share-detail-unlock"].hidden, true);
  assert.ok(text(nodes["share-detail-answers"]).includes("<img src=x onerror=alert(1)>"), "回答は文字として表示する");
  assert.ok(nodes["share-detail-answers"].children[0].className.includes("answer-item"), "質問ごとの枠で表示する");
  assert.equal(nodes["share-stop"].disabled, false);

  // 停止は右上のボタン。確認してから停止する。
  await nodes["share-stop"].fire("click");
  assert.equal(state.stopped, 0);
  assert.ok(stopPrompts.at(-1).includes("この共有を停止しますか"));
  confirmStop = true;
  await nodes["share-stop"].fire("click");
  assert.equal(state.stopped, 1);
  assert.equal(nodes["qr-shares"].querySelectorAll("button")[0].dataset.shareState, "stopped");
  assert.equal(nodes["share-stop"].disabled, true);
  assert.equal(nodes["share-stop"].textContent, "共有停止済み");
  nodes["share-close"].fire("click");
  assert.equal(nodes["share-detail"].open, false);
  assert.equal(nodes["share-detail-answers"].children.length, 0);
  assert.equal(opener.focused, true);

  // 2回目以降は、解除済みなので、本人確認を求めない。
  const calls = state.unlockCalls;
  await nodes["qr-shares"].querySelectorAll("button")[0].fire("click");
  assert.equal(state.unlockCalls, calls);
  nodes["share-close"].fire("click");

  // 旧形式の共有（控えがない）
  state.shares.push({ ...state.shares[0], id: "legacy", status: "expired" });
  await nodes["refresh-shares"].fire("click");
  await nodes["qr-shares"].querySelectorAll("button")[1].fire("click");
  assert.ok(nodes["share-detail-status"].textContent.includes("旧形式"));
  let cancelled = false;
  nodes["share-detail"].fire("cancel", { preventDefault() { cancelled = true; } });
  assert.ok(cancelled);
  assert.equal(nodes["share-detail"].open, false);

  // アカウントが変わったら、一覧を消して操作できなくする。
  state.userId = "other-owner";
  await nodes["refresh-shares"].fire("click");
  assert.equal(nodes["share-start"].disabled, true);
  assert.equal(nodes["qr-shares"].children.length, 0);
  assert.ok(nodes["lock-state"].textContent.includes("アカウントが変わりました"));
}

// --- 発行済みがない／ログインしていない ---
{
  const { nodes, state, bind } = setup();
  state.shares = [];
  await bind();
  assert.ok(text(nodes["qr-shares"]).includes("発行済みの共有はありません"));
}
{
  const { nodes, bind } = setup({ authenticated: false });
  await bind();
  assert.equal(nodes["login-here"].hidden, false);
  assert.equal(nodes["share-start"].disabled, true);
  assert.equal(nodes["refresh-shares"].disabled, true);
}
console.log("Sharing page interactions passed");
