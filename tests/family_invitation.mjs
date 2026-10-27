import assert from "node:assert/strict";
import { bindFamilyInvitation } from "../app/static/js/watashi/family-invitation.mjs";

function page() {
  const events = {};
  const nodes = Object.fromEntries(["family-invitation", "invite-session", "invite-status", "invite-guest",
    "invite-login", "invite-confirmation", "accept-invitation"].map(id => [id, {
    textContent: "", disabled: true, hidden: false, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    fire(name) { return this.listeners[name](); },
  }]));
  return {
    nodes, querySelector: selector => nodes[selector.slice(1)],
    defaultView: {
      addEventListener(name, callback) { events[name] = callback; },
      dispatch(name) { events[name]?.(); },
    },
  };
}
const token = "a".repeat(43);
const address = { origin: "http://localhost", pathname: "/family/invite", hash: `#token=${token}` };
const navigations = [];
function navigationFor(target, paths = navigations) {
  return {
    replaceState(_state, _title, path) {
      paths.push(path);
      const next = new URL(path, target.origin);
      target.pathname = next.pathname;
      target.hash = next.hash;
      // Like the browser History API, replaceState does not emit hashchange.
    },
  };
}
const history = navigationFor(address);
let account = { authenticated: false };
let posts = [];
async function api(path, options = {}) {
  if (path.endsWith("/session")) return account;
  assert.equal(path, "/api/v1/family/invitations/request");
  assert.equal(options.method, "POST");
  posts.push(options.body);
  return { invitation: { status: "awaiting_sender" } };
}
const guest = page();
await bindFamilyInvitation(guest, api, address, history);
assert.equal(guest.nodes["invite-guest"].hidden, false);
assert.equal(guest.nodes["invite-confirmation"].hidden, true);
const login = new URL(guest.nodes["invite-login"].href, address.origin);
assert.equal(login.searchParams.get("next"), "/family/invite");
assert.ok(!login.search.includes(token));
assert.equal(new URLSearchParams(login.hash.slice(1)).get("return-fragment"), `token=${token}`);
await guest.nodes["accept-invitation"].fire("click");
assert.equal(posts.length, 0);
assert.equal(guest.nodes["accept-invitation"].disabled, true);
// Returning from login presents confirmation and never auto-accepts.
account = { authenticated: true, user_id: "person-a", display_name: "サンプルA" };
const loggedIn = page();
const loggedInAddress = { ...address };
await bindFamilyInvitation(loggedIn, api, loggedInAddress, navigationFor(loggedInAddress));
assert.ok(loggedIn.nodes["invite-session"].textContent.includes("サンプルA でログイン中"));
assert.equal(loggedIn.nodes["invite-confirmation"].hidden, false);
assert.equal(posts.length, 0);
account = { authenticated: true, user_id: "person-b", display_name: "サンプルB" };
await loggedIn.nodes["accept-invitation"].fire("click");
assert.equal(posts.length, 0);
assert.ok(loggedIn.nodes["invite-session"].textContent.includes("サンプルB"));
await loggedIn.nodes["accept-invitation"].fire("click");
assert.deepEqual(posts, [{ token }]);
assert.equal(loggedIn.nodes["invite-confirmation"].hidden, true);
assert.equal(navigations.at(-1), "/family/invite");
assert.equal(loggedInAddress.hash, "");
const successMessage = "招待を承諾しました。招待した方の確認をお待ちください。";
assert.equal(loggedIn.nodes["invite-status"].textContent, successMessage);
assert.equal(loggedIn.nodes["invite-session"].textContent, "サンプルB でログイン中");
assert.equal(loggedIn.nodes["accept-invitation"].disabled, true);
await loggedIn.nodes["accept-invitation"].fire("click");
assert.equal(posts.length, 1);
assert.equal(loggedIn.nodes["invite-status"].textContent, successMessage);
const invalid = page();
await bindFamilyInvitation(invalid, () => { throw new Error("Invalid links must not call APIs"); }, { ...address, hash: "" }, history);
assert.ok(invalid.nodes["invite-session"].textContent.includes("確認できません"));

const otherToken = "b".repeat(43);
for (const dispatch of [true, false]) {
  const changed = page();
  const changedAddress = { ...address };
  await bindFamilyInvitation(changed, api, changedAddress, navigationFor(changedAddress));
  const before = posts.length;
  changedAddress.hash = `#token=${otherToken}`;
  if (dispatch) changed.defaultView.dispatch("hashchange");
  await changed.nodes["accept-invitation"].fire("click");
  assert.equal(posts.length, before);
  assert.equal(changed.nodes["accept-invitation"].disabled, true);
  assert.equal(changed.nodes["invite-confirmation"].hidden, true);
  assert.ok(changed.nodes["invite-status"].textContent.includes("再読み込み"));
  // Going back to the first fragment does not reinstate a stale confirmation.
  changedAddress.hash = address.hash;
  changed.defaultView.dispatch("hashchange");
  await changed.nodes["accept-invitation"].fire("click");
  assert.equal(posts.length, before);
}

// A late session/acceptance result must not restore an old invitation's controls,
// replace the new fragment, or show success for the new invitation.
for (const phase of ["initial-session", "accept-session", "accept-post"]) {
  for (const fails of [false, true]) {
    const changed = page(), changedAddress = { ...address };
    let release, reject, reached;
    const pending = new Promise((yes, no) => { release = yes; reject = no; });
    const waiting = new Promise(resolve => { reached = resolve; });
    let sessionCalls = 0;
    const sent = [], replaced = [];
    async function delayedApi(path, options = {}) {
      if (path.endsWith("/session")) {
        sessionCalls++;
        if ((phase === "initial-session" && sessionCalls === 1) || (phase === "accept-session" && sessionCalls === 2)) {
          reached();
          return pending;
        }
        return account;
      }
      sent.push(options.body);
      reached();
      return pending;
    }
    const binding = bindFamilyInvitation(changed, delayedApi, changedAddress, navigationFor(changedAddress, replaced));
    let acceptance;
    if (phase !== "initial-session") {
      await binding;
      acceptance = changed.nodes["accept-invitation"].fire("click");
    }
    await waiting;
    changedAddress.hash = `#token=${otherToken}`;
    changed.defaultView.dispatch("hashchange");
    if (fails) reject(new Error("遅れて届いた失敗"));
    else release(phase === "accept-post" ? {} : account);
    await binding;
    await acceptance;
    assert.deepEqual(sent, phase === "accept-post" ? [{ token }] : []);
    assert.equal(replaced.length, 0);
    assert.equal(changedAddress.hash, `#token=${otherToken}`);
    assert.equal(changed.nodes["accept-invitation"].disabled, true);
    assert.equal(changed.nodes["invite-confirmation"].hidden, true);
    assert.ok(changed.nodes["invite-status"].textContent.includes("再読み込み"));
    await changed.nodes["accept-invitation"].fire("click");
    assert.equal(sent.length, phase === "accept-post" ? 1 : 0);
  }
}
console.log("Invitation confirmation flow passed");
