import assert from "node:assert/strict";
import { bindFamilyShare } from "../app/static/js/watashi/family-share.mjs";

class Element {
  children = [];
  listeners = {};
  textContent = "";
  checked = false;
  disabled = false;
  hidden = false;
  value = "";
  dataset = {};
  attributes = {};
  classList = { toggle() {} };
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name, event = {}) { return this.listeners[name](event); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes[name] = value; }
  get selectedOptions() { return this.children.filter(child => child.tagName === "OPTION" && child.value === this.value); }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    if (selector === "*") return descendants;
    return descendants.filter(child => child.tagName === "INPUT" && (!selector.includes(":checked") || child.checked));
  }
}

const ids = ["family-share-page", "share-steps", "lock-state", "status", "step-unlock", "step-select", "step-target",
  "step-confirm", "step-done", "login-here", "unlock-here", "chapters", "questions", "all-answers", "no-family",
  "connection", "expires-at", "expires-unlimited", "preview-output", "confirm-share", "done-text", "step-back", "step-next", "share", "share-title"];

function setup({ updateId = null, authenticated = true, unlockFails = false } = {}) {
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  const scopes = ["all", "chapters", "questions"].map(value => Object.assign(new Element(), { tagName: "INPUT", value, checked: false }));
  const root = {
    querySelector(selector) {
      if (selector === "input[name=scope]:checked") return scopes.find(input => input.checked);
      return nodes[selector.slice(1)];
    },
    querySelectorAll: selector => (selector === "input[name=scope]" ? scopes : []),
    createElement(tag) { const node = new Element(); node.tagName = tag.toUpperCase(); node.ownerDocument = root; return node; },
    createTextNode(text) { const node = new Element(); node.textContent = text; return node; },
  };
  const state = { unlocked: false, unlockCalls: 0, allowUnlock: false, keyId: "key-A", reauth: false, created: [], updated: [], shares: [{
    id: "share-1", status: "active", connection_id: "conn-1", recipient_user_id: "user-2",
    recipient_display_name: "家族A", current_version: 3, expires_at: null,
  }] };
  const client = {
    getUnlockState: () => ({ unlocked: state.unlocked }),
    async unlock() {
      state.unlockCalls += 1;
      if (unlockFails && !state.allowUnlock) throw Object.assign(new Error("cancel"), { name: "NotAllowedError" });
      state.unlocked = true;
    },
    async api(path) {
      if (path.endsWith("/auth/session")) return authenticated ? { authenticated: true, user_id: "u1", display_name: "テスト" } : { authenticated: false };
      if (path.endsWith("/answers/questions")) return { categories: [{ id: "c1", name: "章1" }],
        questions: [{ id: "q1", category_id: "c1", text: "質問1" }, { id: "q2", category_id: "c1", text: "質問2" }] };
      if (path.endsWith("/answers/current")) return { revision: 1, document: { ciphertext: "x".repeat(70) } };
      if (path.endsWith("/family/connections")) return { connections: [
        { id: "conn-1", status: "active", other_user_id: "user-2", other_display_name: "家族A" },
        { id: "conn-2", status: "disconnected", other_user_id: "user-3", other_display_name: "外した家族" },
      ] };
      if (path.endsWith("/family/shares")) return { shares: state.shares };
      if (path.endsWith("/encryption-keys/user-2")) return { keys: [{ id: state.keyId, status: "active" }] };
      throw new Error(`Unexpected API: ${path}`);
    },
    async loadAnswerDocument() {
      if (state.reauth) throw Object.assign(new Error("再認証"), { code: "reauth_required" });
      return { revision: 1, document: { schema: 1, items: {
        q1: { state: "answered", value: "<b>回答1</b>" }, q2: { state: "answered", value: "回答2" } } } };
    },
    async createFamilyShare(options) { state.created.push(structuredClone(options)); },
    async updateFamilyShare(options) { state.updated.push(structuredClone(options)); },
  };
  for (const node of Object.values(nodes)) node.ownerDocument = root;
  return { nodes, scopes, root, state, client, bind: () => bindFamilyShare(root, client, { updateId }) };
}
const text = element => [element.textContent, ...element.children.map(text)].join("\n");
const visibleSteps = nodes => ["unlock", "select", "target", "confirm", "done"].filter(id => !nodes[`step-${id}`].hidden);
const labels = nodes => nodes["share-steps"].children.map(item => item.children[0].textContent);
const chip = (nodes, index) => nodes["share-steps"].children[index].children[0];
const pick = async (nodes, scopes, value) => {
  scopes.forEach(input => { input.checked = input.value === value; });
  await scopes[0].fire("change");
};
const click = (nodes, id) => nodes[id].fire("click");

// --- 新しい共有：ロック解除 → 内容 → 家族と期限 → 再確認 → 確定 ---
{
  const { nodes, scopes, state, bind } = setup();
  await bind();
  // 開いたらすぐ解除を始め、解除できたら共有範囲の選択へ進む。
  assert.equal(state.unlockCalls, 1);
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  assert.deepEqual(labels(nodes), ["1. ロック解除", "2. 共有内容の選択", "3. 家族と期限", "4. 再確認"]);
  assert.equal(nodes["share-steps"].children[1].dataset.stepState, "current");
  assert.equal(nodes["share-steps"].children[0].dataset.stepState, "done");
  assert.ok(nodes.status.textContent.includes("共有範囲を選択してください"));
  assert.ok(text(nodes.questions).includes("<b>回答1</b>"), "解除後は回答を表示する（文字として）");
  // 範囲は最初は未選択。選ぶまで一覧も出さず、次へも進めない。
  assert.equal(scopes.some(input => input.checked), false);
  assert.equal(nodes.chapters.hidden && nodes.questions.hidden && nodes["all-answers"].hidden, true);
  assert.equal(nodes["step-next"].disabled, true);
  await pick(nodes, scopes, "questions");
  assert.equal(nodes.questions.hidden, false);
  assert.equal(nodes["step-next"].disabled, true, "質問を選ぶまで次へ進めない");
  // 終わったロック解除の手順には、押して戻れる。先の手順は押せない。
  assert.equal(chip(nodes, 0).disabled, false);
  assert.equal(chip(nodes, 1).disabled, true);
  assert.equal(chip(nodes, 2).disabled, true);
  await chip(nodes, 0).fire("click");
  assert.deepEqual(visibleSteps(nodes), ["unlock"]);
  assert.equal(nodes["unlock-here"].textContent, "解除済み");
  assert.equal(state.unlockCalls, 1, "戻っても、解除をやり直さない");
  await click(nodes, "step-next");
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  // 家族の選択肢は共有できる家族だけ。
  assert.deepEqual(nodes.connection.children.map(option => option.textContent), ["家族A"]);
  nodes.connection.value = nodes.connection.children[0].value;
  nodes.questions.querySelectorAll("input")[0].checked = true;
  await nodes.questions.fire("change");
  assert.equal(nodes["step-next"].disabled, false);
  await click(nodes, "step-next");
  assert.deepEqual(visibleSteps(nodes), ["target"]);
  assert.equal(nodes["step-next"].disabled, false);
  // 選んだ手順にも、押して戻れる。
  await chip(nodes, 1).fire("click");
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  assert.equal(nodes.questions.querySelectorAll("input")[0].checked, true, "戻っても選択は残る");
  await click(nodes, "step-next");
  nodes["expires-at"].value = new Date(Date.now() - 3600000).toISOString();
  await nodes["expires-at"].fire("input");
  assert.equal(nodes["step-next"].disabled, true, "過去の期限では進めない");
  nodes["expires-at"].value = new Date(Date.now() + 86400000).toISOString();
  await nodes["expires-at"].fire("input");
  assert.equal(nodes["step-next"].disabled, false);
  // 「無制限」で期限を空にして、期限なしにできる。
  await click(nodes, "expires-unlimited");
  assert.equal(nodes["expires-at"].value, "");
  assert.equal(nodes["step-next"].disabled, false);
  nodes["expires-at"].value = new Date(Date.now() + 86400000).toISOString();
  await nodes["expires-at"].fire("input");
  await click(nodes, "step-next");
  // 再確認
  assert.deepEqual(visibleSteps(nodes), ["confirm"]);
  assert.equal(nodes["step-next"].hidden, true);
  assert.equal(nodes["share"].hidden, false);
  assert.equal(nodes["share"].disabled, true, "確認のチェックまで確定できない");
  const preview = text(nodes["preview-output"]);
  assert.ok(preview.includes("相手：家族A") && preview.includes("鍵：key-A") && preview.includes("範囲：質問"));
  assert.ok(preview.includes("期限：") && !preview.includes("期限：なし"));
  assert.ok(preview.includes("<b>回答1</b>") && !preview.includes("回答2"));
  assert.equal(nodes["share-steps"].children[3].dataset.stepState, "current");
  // 手順の表示から戻っても、確認は消える。
  await chip(nodes, 1).fire("click");
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  assert.equal(nodes["preview-output"].children.length, 0);
  await click(nodes, "step-next");
  await click(nodes, "step-next");
  // 戻ると確認が消え、進み直すと作り直す。
  await click(nodes, "step-back");
  assert.deepEqual(visibleSteps(nodes), ["target"]);
  assert.equal(nodes["preview-output"].children.length, 0);
  await click(nodes, "step-next");
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  assert.equal(nodes["share"].disabled, false);
  // 確認のあとに相手の鍵が変わったら、確定せず確認からやり直す。
  state.keyId = "key-B";
  await click(nodes, "share");
  assert.equal(state.created.length, 0);
  assert.deepEqual(visibleSteps(nodes), ["target"]);
  assert.ok(nodes.status.textContent.includes("もう一度確認"));
  assert.equal(nodes["confirm-share"].checked, false);
  await click(nodes, "step-next");
  assert.ok(text(nodes["preview-output"]).includes("鍵：key-B"));
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  await click(nodes, "share");
  assert.equal(state.created.length, 1);
  assert.deepEqual(state.created[0].selection, { scope: "questions", ids: ["q1"] });
  assert.deepEqual(Object.keys(state.created[0].payload.items), ["q1"]);
  assert.equal(state.created[0].recipientKeyId, "key-B");
  assert.equal(state.created[0].connectionId, "conn-1");
  assert.ok(state.created[0].expiresAt.endsWith("Z"));
  assert.deepEqual(visibleSteps(nodes), ["done"]);
  assert.ok(nodes["done-text"].textContent.includes("家族A"));
  assert.equal(nodes["share"].hidden, true);
  assert.equal(nodes["step-back"].hidden, true);
  assert.equal(nodes["share-steps"].children.every(item => item.dataset.stepState === "done"), true);
  assert.equal(nodes["share-steps"].children.every(item => item.children[0].disabled), true, "共有後は手順へ戻れない");
}

// --- 期限なし・章の選択・全体、本人確認が切れたとき ---
{
  const { nodes, scopes, state, bind } = setup();
  await bind();
  await pick(nodes, scopes, "chapters");
  assert.equal(nodes.chapters.hidden, false);
  assert.equal(nodes.questions.hidden, true);
  assert.equal(nodes["step-next"].disabled, true);
  nodes.chapters.querySelectorAll("input")[0].checked = true;
  await nodes.chapters.fire("change");
  nodes.connection.value = nodes.connection.children[0].value;
  await click(nodes, "step-next");
  await click(nodes, "step-next");
  assert.ok(text(nodes["preview-output"]).includes("期限：なし"));
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  state.reauth = true;
  await click(nodes, "share");
  assert.equal(state.created.length, 0);
  assert.deepEqual(visibleSteps(nodes), ["unlock"], "本人確認が切れたら、最初のロック解除に戻る");
  assert.equal(nodes["unlock-here"].disabled, false);
  assert.ok(nodes.status.textContent.includes("もう一度解除"));
  assert.equal(nodes["preview-output"].children.length, 0);
  await pick(nodes, scopes, "all");
  assert.equal(nodes["all-answers"].hidden, false);
}

// --- 自動の解除を取り消したとき：ボタンで続けられる ---
{
  const { nodes, state, bind } = setup({ unlockFails: true });
  await bind();
  assert.equal(state.unlockCalls, 1);
  assert.deepEqual(visibleSteps(nodes), ["unlock"]);
  assert.ok(nodes.status.textContent.includes("もう一度"));
  assert.equal(nodes["unlock-here"].disabled, false);
  assert.equal(nodes["step-next"].disabled, true);
  state.allowUnlock = true;
  await click(nodes, "unlock-here");
  assert.equal(state.unlockCalls, 2);
  assert.deepEqual(visibleSteps(nodes), ["select"], "手動で解除しても、共有範囲の選択へ進む");
}

// --- ログインしていない ---
{
  const { nodes, bind } = setup({ authenticated: false });
  await bind();
  assert.equal(nodes["login-here"].hidden, false);
  assert.equal(nodes["unlock-here"].hidden, true);
  assert.equal(nodes["step-next"].disabled, true);
  assert.equal(chip(nodes, 0).disabled, true);
}

// --- 共有の更新：家族と期限の手順は省く ---
{
  const { nodes, scopes, state, bind } = setup({ updateId: "share-1" });
  await bind();
  assert.deepEqual(labels(nodes), ["1. ロック解除", "2. 共有内容の選択", "3. 再確認"]);
  assert.ok(nodes["share-title"].textContent.includes("家族A"));
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  await pick(nodes, scopes, "questions");
  nodes.questions.querySelectorAll("input")[1].checked = true;
  await nodes.questions.fire("change");
  await click(nodes, "step-next");
  assert.deepEqual(visibleSteps(nodes), ["confirm"]);
  assert.ok(text(nodes["preview-output"]).includes("更新では変わりません"));
  state.keyId = "key-C";
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  await click(nodes, "share");
  assert.equal(state.updated.length, 0, "確認のあとに鍵が変わったら更新しない");
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  await click(nodes, "step-next");
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  await click(nodes, "share");
  assert.equal(state.updated.length, 1);
  assert.equal(state.updated[0].shareId, "share-1");
  assert.equal(state.updated[0].expectedVersion, 3);
  assert.equal(state.updated[0].recipientKeyId, "key-C");
  assert.deepEqual(Object.keys(state.updated[0].payload.items), ["q2"]);
  assert.equal(state.created.length, 0);
  assert.deepEqual(visibleSteps(nodes), ["done"]);
}

// --- 更新する共有が見つからない・停止済み・期限切れ ---
for (const shares of [[], [{ id: "share-1", status: "stopped", connection_id: "conn-1", recipient_user_id: "user-2" }],
  [{ id: "share-1", status: "active", connection_id: "conn-1", recipient_user_id: "user-2", current_version: 3,
    expires_at: new Date(Date.now() - 60000).toISOString() }]]) {
  const { nodes, state, bind } = setup({ updateId: "share-1" });
  state.shares = shares;
  await bind();
  assert.ok(nodes.status.textContent.includes("更新する共有が見つかりません"));
  assert.equal(state.unlockCalls, 0, "更新できない共有では、解除を始めない");
  await click(nodes, "unlock-here");
  assert.equal(nodes["step-next"].disabled, true, "更新できない共有では、解除しても先へ進めない");
  assert.equal(state.updated.length, 0);
}
console.log("Family share page flow passed");
