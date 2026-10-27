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

function page({ resume = false, stage = null, failEnd = false } = {}) {
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
  async function call(path) {
    calls.push(path);
    if (path === "/api/v1/qr-view/current") { await pause("session"); return current; }
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
  return { nodes, storage, gate, reached, endGate, calls };
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
console.log("QR viewer termination races passed");
