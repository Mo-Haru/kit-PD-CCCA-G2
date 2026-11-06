import assert from "node:assert/strict";
import { bindSharingNew } from "../app/static/js/watashi/sharing-new.mjs";
import { expiryBounds, longExpiryDefault } from "../app/static/js/watashi/sharing-flow.mjs";

class Element {
  children = [];
  listeners = {};
  textContent = "";
  className = "";
  checked = false;
  disabled = false;
  hidden = false;
  value = "";
  min = "";
  max = "";
  dataset = {};
  attributes = {};
  classList = { toggle() {} };
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name, event = {}) { return this.listeners[name](event); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this[name]; }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    if (selector === "*") return descendants;
    return descendants.filter(child => child.tagName === "INPUT" && (!selector.includes(":checked") || child.checked));
  }
}

const ids = ["sharing-new-page", "share-steps", "lock-state", "status", "step-unlock", "step-select", "step-type",
  "step-confirm", "step-done", "login-here", "unlock-here", "chapters", "questions", "all-answers", "long-warning",
  "expiry-field", "expires-at", "preview-output", "confirm-share", "link-facts", "qr-image", "share-url", "copy-link",
  "open-link", "step-back", "step-next", "create-qr", "site-alert", "site-alert-text"];

function setup({ authenticated = true, unlockFails = false } = {}) {
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  const make = (name, values, checkedValue) => values.map(value =>
    Object.assign(new Element(), { tagName: "INPUT", name, value, checked: value === checkedValue }));
  const scopes = make("scope", ["all", "chapters", "questions"], null);
  const modes = make("mode", ["emergency", "timed", "long"], "emergency");
  const root = {
    querySelector(selector) {
      if (selector === "input[name=scope]:checked") return scopes.find(input => input.checked);
      if (selector === "input[name=mode]:checked") return modes.find(input => input.checked);
      return nodes[selector.slice(1)];
    },
    querySelectorAll: selector => (selector === "input[name=scope]" ? scopes : selector === "input[name=mode]" ? modes : []),
    createElement(tag) { const node = new Element(); node.tagName = tag.toUpperCase(); node.ownerDocument = root; return node; },
    createTextNode(text) { const node = new Element(); node.textContent = text; return node; },
  };
  for (const node of Object.values(nodes)) node.ownerDocument = root;
  const state = { unlocked: false, unlockCalls: 0, allowUnlock: !unlockFails, userId: "owner", created: [], rendered: [],
    failCreate: false, reauth: false, source: { schema: 1, items: {
      q1: { state: "answered", value: "<b>回答1</b>" }, q2: { state: "answered", value: "回答2" } } } };
  const client = {
    getUnlockState: () => ({ unlocked: state.unlocked }),
    async unlock() {
      state.unlockCalls += 1;
      if (!state.allowUnlock) throw Object.assign(new Error("cancel"), { name: "NotAllowedError" });
      state.unlocked = true;
    },
    async api(path) {
      if (path.endsWith("/auth/session")) return authenticated ? { authenticated: true, user_id: state.userId, display_name: "テスト" } : { authenticated: false };
      if (path.endsWith("/answers/questions")) return { categories: [{ id: "c1", name: "章1" }],
        questions: [{ id: "q1", category_id: "c1", text: "質問1" }, { id: "q2", category_id: "c1", text: "質問2" }] };
      if (path.endsWith("/answers/current")) return { revision: 1, document: { ciphertext: "x".repeat(70) } };
      throw new Error(`Unexpected API: ${path}`);
    },
    async loadAnswerDocument() { return { revision: 1, document: structuredClone(state.source) }; },
    async createQrShare(options) {
      if (state.failCreate) throw new Error("保存失敗のテスト");
      if (state.reauth) throw Object.assign(new Error("再認証"), { code: "reauth_required" });
      state.created.push(structuredClone(options));
      return { url: "http://localhost/view/test#fixture-only", share: { id: "s1", mode: options.mode, created_at: "2026-11-06" } };
    },
    renderQr(url, options) {
      state.rendered.push({ url, doc: options.doc });
      const image = new Element();
      image.tagName = "CANVAS";
      image.qrUrl = url;
      return image;
    },
  };
  return { nodes, scopes, modes, root, state, client, bind: () => bindSharingNew(root, client) };
}
const text = element => [element.textContent, ...element.children.map(text)].join("\n");
const visibleSteps = nodes => ["unlock", "select", "type", "confirm", "done"].filter(id => !nodes[`step-${id}`].hidden);
const chip = (nodes, index) => nodes["share-steps"].children[index].children[0];
const click = (nodes, id) => nodes[id].fire("click");
const pick = async (scopes, value) => {
  scopes.forEach(input => { input.checked = input.value === value; });
  await scopes[0].fire("change");
};
const setMode = async (modes, value) => {
  modes.forEach(input => { input.checked = input.value === value; });
  await modes[0].fire("change");
};
const futureLocal = days => {
  const date = new Date(Date.now() + days * 86400000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

// --- 緊急用：ロック解除 → 内容 → 種類と期限 → 再確認 → 確定 ---
{
  const { nodes, scopes, state, bind } = setup();
  await bind();
  // 開いたらすぐ解除を始め、解除できたら共有範囲の選択へ進む。
  assert.equal(state.unlockCalls, 1);
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  assert.deepEqual(nodes["share-steps"].children.map(item => item.children[0].textContent),
    ["1. ロック解除", "2. 共有内容の選択", "3. リンクの種類と期限", "4. 再確認"]);
  assert.equal(nodes["share-steps"].children[1].dataset.stepState, "current");
  assert.ok(nodes.status.textContent.includes("共有範囲を選択してください"));
  assert.ok(text(nodes.questions).includes("回答：<b>回答1</b>"), "解除後は回答を文字として表示する");
  // 範囲は最初は未選択。
  assert.equal(scopes.some(input => input.checked), false);
  assert.equal(nodes.questions.hidden && nodes.chapters.hidden && nodes["all-answers"].hidden, true);
  assert.equal(nodes["step-next"].disabled, true);
  await pick(scopes, "questions");
  assert.equal(nodes["step-next"].disabled, true, "質問を選ぶまで次へ進めない");
  nodes.questions.querySelectorAll("input")[0].checked = true;
  await nodes.questions.fire("change");
  assert.equal(nodes["step-next"].disabled, false);
  // 終わった手順へは押して戻れる。先へは進めない。
  assert.equal(chip(nodes, 0).disabled, false);
  assert.equal(chip(nodes, 2).disabled, true);
  await chip(nodes, 0).fire("click");
  assert.deepEqual(visibleSteps(nodes), ["unlock"]);
  assert.equal(state.unlockCalls, 1, "戻っても、解除をやり直さない");
  await click(nodes, "step-next");
  await click(nodes, "step-next");
  assert.deepEqual(visibleSteps(nodes), ["type"]);
  assert.equal(nodes["expiry-field"].hidden, true, "緊急用では期限を求めない");
  assert.equal(nodes["step-next"].disabled, false);
  await click(nodes, "step-next");
  // 再確認
  assert.deepEqual(visibleSteps(nodes), ["confirm"]);
  assert.equal(nodes["step-next"].hidden, true);
  assert.equal(nodes["create-qr"].hidden, false);
  assert.equal(nodes["create-qr"].disabled, true, "確認のチェックまで確定できない");
  const preview = text(nodes["preview-output"]);
  assert.ok(preview.includes("リンクの種類：緊急用") && preview.includes("範囲：質問"));
  assert.ok(preview.includes("<b>回答1</b>") && !preview.includes("回答2"), "選んだ質問だけを見せる");
  assert.ok(nodes["preview-output"].children.at(-1).children[0].className.includes("answer-item"), "質問ごとの枠で表示する");
  await chip(nodes, 1).fire("click");
  assert.deepEqual(visibleSteps(nodes), ["select"]);
  assert.equal(nodes["preview-output"].children.length, 0, "戻ると確認が消える");
  await click(nodes, "step-next");
  await click(nodes, "step-next");
  // 確認のあとに回答を保存し直しても、確認した内容だけを共有する。
  state.source.items.q1.value = "確認後に更新された回答";
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  assert.equal(nodes["create-qr"].disabled, false);
  state.failCreate = true;
  await click(nodes, "create-qr");
  // エラーは、ページ本文の枠の上の枠に出す（ページ内の表示欄には出さない）。
  assert.equal(nodes["site-alert"].hidden, false);
  assert.ok(nodes["site-alert-text"].textContent.includes("保存失敗"));
  assert.equal(nodes.status.textContent, "");
  assert.equal(state.rendered.length, 0, "作成に失敗したらQRは描かない");
  assert.deepEqual(visibleSteps(nodes), ["confirm"]);
  state.failCreate = false;
  await click(nodes, "create-qr");
  assert.equal(nodes["site-alert"].hidden, true, "成功したら、エラー表示は消える");
  assert.equal(state.created.length, 1);
  assert.equal(state.created[0].mode, "emergency");
  assert.equal(state.created[0].expiresAt, null);
  assert.deepEqual(state.created[0].selection, { scope: "questions", ids: ["q1"] });
  assert.equal(state.created[0].payload.items.q1.value, "<b>回答1</b>");
  assert.ok(!Object.hasOwn(state.created[0].payload.items, "q2"));
  assert.deepEqual(visibleSteps(nodes), ["done"]);
  // QRにするのは作成で返ったURLだけ。
  assert.deepEqual(state.rendered.map(item => item.url), ["http://localhost/view/test#fixture-only"]);
  assert.equal(state.rendered[0].doc.createElement !== undefined, true);
  assert.equal(nodes["qr-image"].children[0].qrUrl, nodes["share-url"].value);
  assert.equal(nodes["open-link"].href, nodes["share-url"].value);
  assert.ok(nodes["link-facts"].textContent.includes("緊急用"));
  assert.equal(nodes["create-qr"].hidden && nodes["step-back"].hidden && nodes["step-next"].hidden, true);
  assert.equal(nodes["share-steps"].children.every(item => item.children[0].disabled), true, "作成後は手順へ戻れない");
}

// --- 期限付き：期限は必須。過去・範囲外・入力の途中では進めない ---
{
  const { nodes, scopes, modes, state, bind } = setup();
  await bind();
  await pick(scopes, "all");
  await click(nodes, "step-next");
  await setMode(modes, "timed");
  assert.equal(nodes["expiry-field"].hidden, false);
  assert.equal(nodes["expires-at"].value, "", "期限付きは選び直してもらう");
  assert.equal(nodes["step-next"].disabled, true);
  assert.equal(nodes["expires-at"].min, expiryBounds("timed").min);
  nodes["expires-at"].value = futureLocal(-1);
  await nodes["expires-at"].fire("input");
  assert.equal(nodes["step-next"].disabled, true, "過去の期限では進めない");
  nodes["expires-at"].value = futureLocal(40);
  await nodes["expires-at"].fire("input");
  assert.equal(nodes["step-next"].disabled, true, "31日を超える期限では進めない");
  nodes["expires-at"].value = "";
  nodes["expires-at"].validity = { badInput: true };
  await nodes["expires-at"].fire("keyup");
  assert.equal(nodes["step-next"].disabled, true, "日付だけで時刻が空のときは進めない");
  delete nodes["expires-at"].validity;
  nodes["expires-at"].value = futureLocal(3);
  await nodes["expires-at"].fire("input");
  assert.equal(nodes["step-next"].disabled, false);
  await click(nodes, "step-next");
  assert.ok(text(nodes["preview-output"]).includes("リンクの種類：期限付き"));
  assert.deepEqual(Object.keys(state.created), []);
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  // 確認のあとに期限が変えられたら（イベントなしで）、確定せずに種類と期限の手順へ戻す。
  nodes["expires-at"].value = futureLocal(5);
  await click(nodes, "create-qr");
  assert.equal(state.created.length, 0);
  assert.deepEqual(visibleSteps(nodes), ["type"]);
  assert.ok(nodes["site-alert-text"].textContent.includes("もう一度確認"));
  assert.equal(nodes["confirm-share"].checked, false);
  await click(nodes, "step-next");
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  await click(nodes, "create-qr");
  assert.equal(state.created.length, 1);
  assert.equal(state.created[0].mode, "timed");
  assert.equal(new Date(state.created[0].expiresAt).getTime() > Date.now(), true);
  assert.deepEqual(state.created[0].selection, { scope: "all" });
}

// --- 長期：警告を出し、期限の既定は3年後 ---
{
  const { nodes, scopes, modes, state, bind } = setup();
  await bind();
  await pick(scopes, "all");
  await click(nodes, "step-next");
  assert.equal(nodes["long-warning"].hidden, true);
  await setMode(modes, "long");
  assert.equal(nodes["long-warning"].hidden, false);
  assert.equal(nodes["expires-at"].value, longExpiryDefault());
  assert.equal(nodes["step-next"].disabled, false, "既定の期限ですぐに進める");
  await click(nodes, "step-next");
  assert.ok(text(nodes["preview-output"]).includes("誰でも何回でも閲覧できます"));
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  await click(nodes, "create-qr");
  assert.equal(state.created[0].mode, "long");
  assert.equal(new Date(state.created[0].expiresAt).getTime(), new Date(nodes["expires-at"].value).getTime());
}

// --- 本人確認が切れたとき・解除を取り消したとき ---
{
  const { nodes, scopes, state, bind } = setup();
  await bind();
  await pick(scopes, "all");
  await click(nodes, "step-next");
  await click(nodes, "step-next");
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  state.reauth = true;
  await click(nodes, "create-qr");
  assert.equal(state.created.length, 0);
  assert.deepEqual(visibleSteps(nodes), ["unlock"], "本人確認が切れたら、最初のロック解除に戻る");
  assert.ok(nodes["site-alert-text"].textContent.includes("もう一度解除"));
  assert.equal(nodes["preview-output"].children.length, 0);
}
{
  const { nodes, state, bind } = setup({ unlockFails: true });
  await bind();
  assert.deepEqual(visibleSteps(nodes), ["unlock"]);
  assert.ok(nodes["site-alert-text"].textContent.includes("もう一度"));
  assert.equal(nodes["step-next"].disabled, true);
  state.allowUnlock = true;
  await click(nodes, "unlock-here");
  assert.deepEqual(visibleSteps(nodes), ["select"], "手動で解除しても、共有範囲の選択へ進む");
}

// --- ログインしていない／アカウントが変わった ---
{
  const { nodes, bind } = setup({ authenticated: false });
  await bind();
  assert.equal(nodes["login-here"].hidden, false);
  assert.equal(nodes["unlock-here"].hidden, true);
  assert.equal(nodes["step-next"].disabled, true);
}
{
  const { nodes, scopes, state, bind } = setup();
  await bind();
  await pick(scopes, "all");
  await click(nodes, "step-next");
  await click(nodes, "step-next");
  nodes["confirm-share"].checked = true;
  await nodes["confirm-share"].fire("change");
  state.userId = "other-owner";
  await click(nodes, "create-qr");
  assert.equal(state.created.length, 0, "アカウントが変わったら共有しない");
  assert.ok(nodes["site-alert-text"].textContent.includes("アカウントが変わりました"));
  assert.equal(nodes["share-url"].value, "");
  assert.equal(nodes["create-qr"].disabled, true);
}
console.log("Sharing new page flow passed");
