import assert from "node:assert/strict";
import { bytesToB64url } from "../app/static/js/watashi/bytes.mjs";
import { readQrSecrets } from "../app/static/js/watashi/crypto.mjs";
import { bindQrViewPage } from "../app/static/js/watashi/view.mjs";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const shareId = "s".repeat(43);
const payload = { schema: 1, items: { q1: { state: "answered", value: "共有した回答" } } };
const catalog = { questions: [{ id: "q1", text: "質問" }], categories: [] };
const current = { id: shareId, status: "active", updated_at: "2026-10-27", session: {} };

function page({ resume = false, stage = null, failEnd = false, sessionBeforeStart = true } = {}) {
  const storageData = new Map();
  const storage = {
    getItem: key => storageData.get(key) ?? null,
    setItem: (key, value) => storageData.set(key, value),
    removeItem: key => storageData.delete(key),
  };
  if (resume) storage.setItem("wn-qr-opened", shareId);
  const root = { createElement: element, querySelector: selector => nodes[selector.slice(1)] };
  function element() {
    return {
      textContent: "", children: [], dataset: {}, disabled: false, listeners: {}, ownerDocument: root,
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      fire(name) { return this.listeners[name](); },
    };
  }
  const nodes = Object.fromEntries(["viewer", "status", "output", "validity", "updated", "open", "reopen", "end"].map(id => [id, element()]));
  nodes.viewer.dataset.shareId = shareId;
  const gate = deferred(), reached = deferred(), endGate = deferred();
  const calls = [];
  async function pause(name) {
    if (stage === name) { reached.resolve(); await gate.promise; }
  }
  let started = sessionBeforeStart;
  const cancels = [];
  async function call(path, options = {}) {
    calls.push(path);
    if (path === `/api/v1/qr-shares/${shareId}/cancel`) { cancels.push(options); return { ok: true }; }
    if (path === `/api/v1/qr-shares/${shareId}/start`) {
      await pause("start");
      started = true;
      calls.push("(start responded)");
      return { session_id: "view-session" };
    }
    if (path === "/api/v1/qr-view/current") {
      if (!started) throw Object.assign(new Error("閲覧セッションがありません。"), { status: 401, code: "qr_unauthenticated" });
      await pause("session");
      return current;
    }
    if (path === "/api/v1/answers/questions") { await pause("catalog"); return catalog; }
    if (path === "/api/v1/qr-view/end") {
      await endGate.promise;
      if (failEnd) throw new Error("終了APIの失敗");
      return {};
    }
    throw new Error(`Unexpected API: ${path}`);
  }
  bindQrViewPage(root, {
    call, storage,
    address: { pathname: `/view/${shareId}`, hash: `#k=${bytesToB64url(new Uint8Array(32).fill(1))}&t=${bytesToB64url(new Uint8Array(32).fill(2))}` },
    navigation: { replaceState() {} },
    decrypt: async () => { await pause("decrypt"); return payload; },
  });
  return { nodes, storage, gate, reached, endGate, calls, cancels };
}

// Both explicit opening and automatic resumption must ignore every late boundary,
// including errors, after the viewer has ended the session.
for (const resume of [false, true]) {
  for (const stage of ["session", "decrypt", "catalog"]) {
    for (const lateError of [false, true]) {
      const test = page({ resume, stage, failEnd: lateError });
      await flush();
      const opening = resume ? null : test.nodes.open.fire("click");
      await test.reached.promise;
      const ending = test.nodes.end.fire("click");
      const endedMessage = test.nodes.status.textContent;
      assert.ok(endedMessage.includes("閲覧を終了"));
      assert.equal(readQrSecrets(test.storage, shareId), null);
      assert.equal(test.storage.getItem("wn-qr-opened"), null);
      assert.equal(test.nodes.output.children.length, 0);
      // The server's end response is deliberately still pending here.
      if (lateError) test.gate.reject(new Error("遅れて届いた失敗"));
      else test.gate.resolve();
      await opening;
      await flush();
      assert.equal(test.nodes.output.children.length, 0, `${resume}/${stage}/${lateError}`);
      assert.equal(test.nodes.status.textContent, endedMessage);
      assert.equal(test.nodes.validity.textContent, "");
      assert.equal(test.nodes.updated.textContent, "");
      assert.equal(test.storage.getItem("wn-qr-opened"), null);
      assert.equal(readQrSecrets(test.storage, shareId), null);
      assert.equal(test.nodes.open.disabled, true);
      test.endGate.resolve();
      await ending;
      const before = test.calls.length;
      await test.nodes.open.fire("click");
      await test.nodes.reopen.fire("click");
      await test.nodes.end.fire("click");
      assert.equal(test.calls.length, before);
      assert.equal(test.nodes.status.textContent, endedMessage);
    }
  }
}

// Normal viewing and ending an already displayed answer remain available.
for (const resume of [false, true]) {
  const test = page({ resume });
  await flush();
  if (!resume) await test.nodes.open.fire("click");
  await flush();
  assert.ok(test.nodes.output.children[0].textContent.includes("共有した回答"));
  assert.ok(test.nodes.status.textContent.includes("閲覧中"));
  const ending = test.nodes.end.fire("click");
  assert.equal(test.nodes.output.children.length, 0);
  test.endGate.resolve();
  await ending;
}
// The start request is still in flight when the viewer ends: the end request has no session yet,
// so once the late start creates one, the page must end that session on the server too.
for (const lateError of [false, true]) {
  const test = page({ stage: "start", sessionBeforeStart: false, failEnd: lateError });
  await flush();
  const opening = test.nodes.open.fire("click");
  await test.reached.promise;
  const ending = test.nodes.end.fire("click");
  // 開始の応答より前に、サーバーへ試行の取り消しを送る（ページを閉じても届くよう keepalive）。
  assert.equal(test.cancels.length, 1);
  assert.equal(test.cancels[0].keepalive, true);
  assert.equal(test.cancels[0].body.start_token, bytesToB64url(new Uint8Array(32).fill(2)));
  assert.ok(typeof test.cancels[0].body.attempt_id === "string" && test.cancels[0].body.attempt_id.length >= 43);
  test.endGate.resolve();
  await ending;
  const endsBeforeStart = test.calls.filter(path => path === "/api/v1/qr-view/end").length;
  assert.equal(endsBeforeStart, 1);
  test.gate.resolve();
  await opening;
  await flush();
  const startAt = test.calls.indexOf("(start responded)");
  assert.ok(startAt > 0);
  const endsAfterStart = test.calls.slice(startAt + 1).filter(path => path === "/api/v1/qr-view/end").length;
  assert.equal(endsAfterStart, 1, `late start must be ended on the server (${test.calls.join(", ")})`);
  assert.equal(test.nodes.output.children.length, 0);
  assert.equal(readQrSecrets(test.storage, shareId), null);
  assert.ok(test.nodes.status.textContent.includes("閲覧を終了"));
}
console.log("QR viewer termination races passed");
