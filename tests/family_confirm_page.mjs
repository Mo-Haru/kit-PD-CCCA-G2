/**
 * 確認リンクの画面。開いただけでは登録せず、招待した人が本人確認してから登録する。
 */
import assert from "node:assert/strict";
import { bindFamilyConfirm } from "../app/static/js/watashi/family-confirm.mjs";

function page() {
  const nodes = Object.fromEntries(["family-confirm", "confirm-session", "confirm-status", "confirm-guest",
    "confirm-login", "confirm-action", "confirm-link"].map(id => [id, {
    textContent: "", disabled: true, hidden: true, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    fire(name) { return this.listeners[name](); },
  }]));
  const events = {};
  return {
    nodes, querySelector: selector => nodes[selector.slice(1)],
    defaultView: {
      addEventListener(name, callback) { events[name] = callback; },
      dispatch(name) { events[name]?.(); },
    },
  };
}
const token = "k".repeat(43);
function addressFor(hash = `#token=${token}`) {
  return { origin: "http://localhost:5000", pathname: "/family/confirm", hash };
}
function navigationFor(target, paths) {
  return { replaceState(_state, _title, path) { paths.push(path); target.hash = new URL(path, target.origin).hash; } };
}

let account = { authenticated: false };
let posts = [];
let reauths = 0;
let reply = async () => ({ connection_id: "conn-1", recipient_display_name: "申請した家族" });
async function api(path, options = {}) {
  if (path === "/api/v1/auth/session") return account;
  assert.equal(path, "/api/v1/family/invitations/confirm-link");
  assert.equal(options.method, "POST");
  posts.push(options.body);
  return reply();
}
async function reauth() { reauths += 1; }

// Not logged in: offer login that carries the token only in the fragment.
const guest = page();
await bindFamilyConfirm(guest, { call: api, reauth, address: addressFor(), navigation: navigationFor(addressFor(), []) });
assert.equal(guest.nodes["confirm-guest"].hidden, false);
assert.equal(guest.nodes["confirm-action"].hidden, true);
const login = new URL(guest.nodes["confirm-login"].href, "http://localhost:5000");
assert.equal(login.searchParams.get("next"), "/family/confirm");
assert.ok(!login.search.includes(token));
assert.equal(new URLSearchParams(login.hash.slice(1)).get("return-fragment"), `token=${token}`);
await guest.nodes["confirm-link"].fire("click");
assert.equal(posts.length, 0);

// Logged in: opening the page never confirms by itself; the button reauthenticates first.
account = { authenticated: true, user_id: "owner", display_name: "招待した人" };
const address = addressFor();
const paths = [];
const owner = page();
await bindFamilyConfirm(owner, { call: api, reauth, address, navigation: navigationFor(address, paths) });
assert.equal(owner.nodes["confirm-action"].hidden, false);
assert.equal(owner.nodes["confirm-link"].disabled, false);
assert.equal(posts.length, 0);
assert.equal(reauths, 0);

// A different account logged in meanwhile must look again before confirming.
account = { authenticated: true, user_id: "someone-else", display_name: "別の人" };
await owner.nodes["confirm-link"].fire("click");
assert.equal(posts.length, 0);
assert.ok(owner.nodes["confirm-status"].textContent.includes("アカウントが変わりました"));

// Wrong account / stale link: generic message, nothing removed from the address.
reply = async () => { const error = new Error("招待が見つかりません。"); error.code = "not_found"; throw error; };
await owner.nodes["confirm-link"].fire("click");
assert.deepEqual(posts, [{ token }]);
assert.equal(reauths, 1);
assert.ok(owner.nodes["confirm-status"].textContent.includes("招待したアカウントでログインしているか"));
assert.equal(paths.length, 0);
assert.equal(owner.nodes["confirm-link"].disabled, false);

// Cancelled passkey prompt does not send the token.
const cancelled = page();
account = { authenticated: true, user_id: "owner", display_name: "招待した人" };
await bindFamilyConfirm(cancelled, {
  call: api, address: addressFor(), navigation: navigationFor(addressFor(), []),
  reauth: async () => { const error = new Error("cancel"); error.name = "NotAllowedError"; throw error; },
});
const sentBefore = posts.length;
await cancelled.nodes["confirm-link"].fire("click");
assert.equal(posts.length, sentBefore);
assert.ok(cancelled.nodes["confirm-status"].textContent.includes("本人確認を完了できませんでした"));

// Success: names the added family, hides the action, and clears the token from the address bar.
reply = async () => ({ connection_id: "conn-1", recipient_display_name: "申請した家族" });
const success = page();
const successAddress = addressFor();
const successPaths = [];
await bindFamilyConfirm(success, { call: api, reauth, address: successAddress, navigation: navigationFor(successAddress, successPaths) });
await success.nodes["confirm-link"].fire("click");
assert.equal(success.nodes["confirm-status"].textContent, "申請した家族 さんを家族に追加しました。");
assert.equal(success.nodes["confirm-action"].hidden, true);
assert.equal(success.nodes["confirm-link"].disabled, true);
assert.deepEqual(successPaths, ["/family/confirm"]);
assert.equal(successAddress.hash, "");
const sent = posts.length;
await success.nodes["confirm-link"].fire("click");
assert.equal(posts.length, sent);

// Malformed links call nothing.
const invalid = page();
await bindFamilyConfirm(invalid, { call: () => { throw new Error("must not call"); }, reauth, address: addressFor(""), navigation: navigationFor(addressFor(""), []) });
assert.ok(invalid.nodes["confirm-session"].textContent.includes("確認できません"));
// Opening another applicant's link in the same tab only changes the fragment.
// The page must never approve the first applicant from that state.
const otherToken = "m".repeat(43);
for (const dispatch of [true, false]) {
  const changed = page();
  const changedAddress = addressFor();
  const changedPaths = [];
  await bindFamilyConfirm(changed, { call: api, reauth, address: changedAddress, navigation: navigationFor(changedAddress, changedPaths) });
  const before = posts.length;
  const reauthsBefore = reauths;
  changedAddress.hash = `#token=${otherToken}`;
  if (dispatch) changed.defaultView.dispatch("hashchange");
  await changed.nodes["confirm-link"].fire("click");
  assert.equal(posts.length, before);
  assert.equal(reauths, reauthsBefore);
  assert.equal(changed.nodes["confirm-link"].disabled, true);
  assert.equal(changed.nodes["confirm-action"].hidden, true);
  assert.ok(changed.nodes["confirm-status"].textContent.includes("再読み込み"));
  // Returning to the first fragment does not reinstate the old link.
  changedAddress.hash = `#token=${token}`;
  changed.defaultView.dispatch("hashchange");
  await changed.nodes["confirm-link"].fire("click");
  assert.equal(posts.length, before);
  assert.deepEqual(changedPaths, []);
}

// A fragment change while the passkey prompt is open stops before sending the old token.
{
  const changed = page();
  const changedAddress = addressFor();
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  let reached;
  const waiting = new Promise(resolve => { reached = resolve; });
  await bindFamilyConfirm(changed, {
    call: api, address: changedAddress, navigation: navigationFor(changedAddress, []),
    reauth: () => { reached(); return pending; },
  });
  const before = posts.length;
  const click = changed.nodes["confirm-link"].fire("click");
  await waiting;
  changedAddress.hash = `#token=${otherToken}`;
  changed.defaultView.dispatch("hashchange");
  release();
  await click;
  assert.equal(posts.length, before);
  assert.equal(changed.nodes["confirm-link"].disabled, true);
  assert.ok(changed.nodes["confirm-status"].textContent.includes("再読み込み"));
}

// A fragment change after the request was sent reports which applicant was added, without claiming the new link.
{
  const changed = page();
  const changedAddress = addressFor();
  const changedPaths = [];
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  let reached;
  const waiting = new Promise(resolve => { reached = resolve; });
  reply = () => { reached(); return pending; };
  await bindFamilyConfirm(changed, { call: api, reauth, address: changedAddress, navigation: navigationFor(changedAddress, changedPaths) });
  const click = changed.nodes["confirm-link"].fire("click");
  await waiting;
  changedAddress.hash = `#token=${otherToken}`;
  changed.defaultView.dispatch("hashchange");
  release({ connection_id: "conn-2", recipient_display_name: "最初の申請者" });
  await click;
  assert.deepEqual(posts.at(-1), { token });
  assert.ok(changed.nodes["confirm-status"].textContent.includes("変更前の確認リンクで 最初の申請者 さんを家族に追加しました"));
  assert.deepEqual(changedPaths, []);
  assert.equal(changedAddress.hash, `#token=${otherToken}`);
  assert.equal(changed.nodes["confirm-link"].disabled, true);
}
console.log("Family confirmation link page passed");
