import assert from "node:assert/strict";
import { bindSiteAlert, clearSiteAlert, notify } from "../app/static/js/watashi/site-alert.mjs";

class Element {
  textContent = "";
  hidden = true;
  dataset = {};
  listeners = {};
  classes = new Set();
  classList = { toggle: (name, on) => { if (on) this.classes.add(name); else this.classes.delete(name); } };
  addEventListener(name, callback) { this.listeners[name] = callback; }
}
function page({ withAlert = true } = {}) {
  const nodes = { "site-alert": new Element(), "site-alert-text": new Element(), "site-alert-close": new Element(), status: new Element() };
  nodes.status.hidden = false;
  const root = { querySelector: selector => (withAlert || !selector.startsWith("#site-alert") ? nodes[selector.slice(1)] : null) };
  return { nodes, root };
}

// エラーは、ページ本文の枠の上の枠に出す。ページ内の表示欄には出さない。
{
  const { nodes, root } = page();
  notify(root, nodes.status, "保存に失敗しました。", true);
  assert.equal(nodes["site-alert"].hidden, false);
  assert.equal(nodes["site-alert-text"].textContent, "保存に失敗しました。");
  assert.equal(nodes.status.textContent, "");
  assert.equal(nodes.status.classes.has("is-error"), false);
  // 次に、エラーでない結果が出たら、上の枠を消して、ページ内の表示欄に出す。
  notify(root, nodes.status, "保存しました。");
  assert.equal(nodes["site-alert"].hidden, true);
  assert.equal(nodes.status.textContent, "保存しました。");
  // 新しいエラーは、前のエラーを置き換える。
  notify(root, nodes.status, "1回目", true);
  notify(root, nodes.status, "2回目", true);
  assert.equal(nodes["site-alert-text"].textContent, "2回目");
  clearSiteAlert(root);
  assert.equal(nodes["site-alert"].hidden, true);
}
// 上の枠がないページ（ログイン・新規登録など）では、エラーもページ内の表示欄に出す。
{
  const { nodes, root } = page({ withAlert: false });
  notify(root, nodes.status, "ログインできませんでした。", true);
  assert.equal(nodes.status.textContent, "ログインできませんでした。");
  assert.equal(nodes.status.classes.has("is-error"), true);
  notify(root, nodes.status, "ログインしました。");
  assert.equal(nodes.status.classes.has("is-error"), false);
  notify(null, null, "root がなくても止まらない", true);
}
// 閉じるボタン。拾われなかった失敗の表示。
{
  const { nodes, root } = page();
  const win = { listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; } };
  bindSiteAlert(root, win);
  notify(root, nodes.status, "エラー", true);
  nodes["site-alert-close"].listeners.click();
  assert.equal(nodes["site-alert"].hidden, true);
  win.listeners.unhandledrejection({ reason: new Error("どこにも拾われなかった") });
  assert.equal(nodes["site-alert"].hidden, false);
  assert.ok(nodes["site-alert-text"].textContent.includes("予期しないエラー"));
  assert.ok(!nodes["site-alert-text"].textContent.includes("どこにも拾われなかった"), "内部の文言は見せない");
  nodes["site-alert-close"].listeners.click();
  win.listeners.unhandledrejection({ reason: Object.assign(new Error("中断"), { name: "AbortError" }) });
  assert.equal(nodes["site-alert"].hidden, true, "通信の中断は見せない");
  win.listeners.error({ error: new Error("画面の中の例外") });
  assert.equal(nodes["site-alert"].hidden, false);
  // 2回呼んでも、重ねて登録しない。
  const before = Object.keys(win.listeners).length;
  bindSiteAlert(root, win);
  assert.equal(Object.keys(win.listeners).length, before);
}
console.log("Site alert passed");
