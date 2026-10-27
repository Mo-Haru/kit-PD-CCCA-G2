import assert from "node:assert/strict";
import { bindFamilyInvitation } from "../app/static/js/watashi/family-invitation.mjs";

function page() {
  const nodes = Object.fromEntries(["family-invitation", "invite-session", "invite-status", "invite-guest",
    "invite-login", "invite-confirmation", "accept-invitation"].map(id => [id, {
    textContent: "", disabled: true, hidden: false, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    fire(name) { return this.listeners[name](); },
  }]));
  return { nodes, querySelector: selector => nodes[selector.slice(1)] };
}
const token = "a".repeat(43);
const address = { origin: "http://localhost", pathname: "/family/invite", hash: `#token=${token}` };
const navigations = [];
const history = { replaceState(_state, _title, path) { navigations.push(path); } };
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
await bindFamilyInvitation(loggedIn, api, address, history);
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
await loggedIn.nodes["accept-invitation"].fire("click");
assert.equal(posts.length, 1);
const invalid = page();
await bindFamilyInvitation(invalid, () => { throw new Error("Invalid links must not call APIs"); }, { ...address, hash: "" }, history);
assert.ok(invalid.nodes["invite-session"].textContent.includes("確認できません"));
console.log("Invitation confirmation flow passed");
