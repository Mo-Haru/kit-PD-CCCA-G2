import assert from "node:assert/strict";

function element() {
  return {
    textContent: "",
    hidden: false,
    dataset: {},
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    set innerHTML(_value) { throw new Error("Session text must not be rendered as HTML"); },
  };
}

const account = element();
const label = element();
const name = element();
const link = element();
link.href = "/auth?next=%2Ffamily%2Finvite%3Ftab%3Dreceived";
link.dataset.accountUrl = "/account";
const secondaryLink = element();
const menuAccount = element();
const menuLabel = element();
const menuName = element();
const menuLink = element();
globalThis.location = { origin: "http://localhost", hash: "#token=fixture-token" };
const loginHref = link.href + "#return-fragment=token%3Dfixture-token";
const nodes = new Map([
  ["#header-account", account],
  ["#header-session-label", label],
  ["#header-session-name", name],
  ["#header-account-link", link],
  ["#menu-account", menuAccount],
  ["#menu-session-label", menuLabel],
  ["#menu-session-name", menuName],
  ["#menu-account-link", menuLink],
]);
const windowEvents = new Map();
const documentEvents = new Map();
globalThis.window = { addEventListener(type, listener) { windowEvents.set(type, listener); } };
globalThis.document = {
  visibilityState: "visible",
  querySelector(selector) { return nodes.get(selector) || null; },
  querySelectorAll(selector) { assert.equal(selector, "[data-auth-link]"); return [link, menuLink, secondaryLink]; },
  addEventListener(type, listener) { documentEvents.set(type, listener); },
};
let outcome = { authenticated: false };
const requests = [];
globalThis.fetch = async (path, options) => {
  requests.push({ path, options });
  if (outcome instanceof Error) throw outcome;
  if (typeof outcome === "function") return outcome();
  return new Response(JSON.stringify(outcome), { status: 200 });
};
const settle = () => new Promise((resolve) => setImmediate(resolve));

await import("../app/static/js/watashi/header-session.mjs");
await settle();
assert.equal(account.dataset.sessionState, "guest");
assert.equal(label.textContent, "未ログイン");
assert.equal(name.hidden, true);
assert.equal(name.textContent, "");
assert.equal(link.textContent, "ログイン・新規登録");
assert.equal(link.href, loginHref);
assert.equal(secondaryLink.href, loginHref);
assert.equal(menuAccount.dataset.sessionState, "guest");
assert.equal(menuLabel.textContent, "未ログイン");
assert.equal(menuName.hidden, true);
assert.equal(menuName.textContent, "");
assert.equal(menuLink.textContent, "ログイン・新規登録");
assert.equal(menuLink.href, loginHref);

const displayName = "<img src=x onerror=alert(1)>";
outcome = { authenticated: true, display_name: displayName };
await windowEvents.get("focus")();
assert.equal(account.dataset.sessionState, "authenticated");
assert.equal(label.textContent, "ログイン中");
assert.equal(name.textContent, `${displayName} さん`);
assert.equal(name.hidden, false);
assert.equal(link.textContent, "アカウント");
assert.equal(link.href, "/account");
assert.equal(secondaryLink.href, "/account");
assert.equal(menuAccount.dataset.sessionState, "authenticated");
assert.equal(menuLabel.textContent, "ログイン中");
assert.equal(menuName.textContent, `${displayName} さん`);
assert.equal(menuName.hidden, false);
assert.equal(menuLink.textContent, "アカウント");
assert.equal(menuLink.href, "/account");

outcome = new Error("Network unavailable");
await windowEvents.get("focus")();
assert.equal(account.dataset.sessionState, "error");
assert.equal(label.textContent, "ログイン状態を確認できません");
assert.equal(name.hidden, true);
assert.equal(name.textContent, "");
assert.equal(account.attributes["aria-busy"], "false");
assert.equal(link.href, loginHref);
assert.equal(menuAccount.dataset.sessionState, "error");
assert.equal(menuName.hidden, true);
assert.equal(menuName.textContent, "");
assert.equal(menuLabel.textContent, label.textContent);
assert.equal(menuLink.textContent, "ログイン・アカウント");
assert.equal(menuLink.href, loginHref);
assert.equal(menuAccount.attributes["aria-busy"], "false");

outcome = {};
await windowEvents.get("focus")();
assert.equal(account.dataset.sessionState, "error");

outcome = { authenticated: false };
windowEvents.get("pageshow")({ persisted: true });
await settle();
assert.equal(account.dataset.sessionState, "guest");
assert.equal(link.textContent, "ログイン・新規登録");
assert.equal(link.href, loginHref);
location.hash = "";
windowEvents.get("hashchange")();
assert.equal(link.href, "/auth?next=%2Ffamily%2Finvite%3Ftab%3Dreceived");
assert.equal(menuLink.href, link.href);

const before = requests.length;
document.visibilityState = "hidden";
documentEvents.get("visibilitychange")();
windowEvents.get("pageshow")({ persisted: false });
await settle();
assert.equal(requests.length, before);

outcome = { authenticated: true, display_name: "再ログイン" };
document.visibilityState = "visible";
documentEvents.get("visibilitychange")();
await settle();
assert.equal(name.textContent, "再ログイン さん");
assert.equal(menuName.textContent, "再ログイン さん");

let release;
outcome = () => new Promise((resolve) => { release = resolve; });
const refreshing = windowEvents.get("focus")();
assert.equal(account.attributes["aria-busy"], "true");
assert.equal(menuAccount.attributes["aria-busy"], "true");
const inFlightCount = requests.length;
await windowEvents.get("focus")();
documentEvents.get("visibilitychange")();
assert.equal(requests.length, inFlightCount);
release(new Response(JSON.stringify({ authenticated: false }), { status: 200 }));
await refreshing;
assert.equal(name.textContent, "");
assert.equal(name.hidden, true);
assert.equal(account.dataset.sessionState, "guest");
assert.equal(menuName.textContent, "");
assert.equal(menuName.hidden, true);
assert.equal(menuAccount.dataset.sessionState, "guest");
assert.equal(menuAccount.attributes["aria-busy"], "false");

assert.ok(requests.every(({ path, options }) => path === "/api/v1/auth/session"
  && options.method === "GET" && options.body === undefined && options.credentials === "same-origin"));
nodes.clear();
windowEvents.clear();
documentEvents.clear();
const withoutHeaderCount = requests.length;
await import("../app/static/js/watashi/header-session.mjs?without-header");
assert.equal(requests.length, withoutHeaderCount);
assert.equal(windowEvents.size, 0);
assert.equal(documentEvents.size, 0);
console.log("PASS: header guest/name/error/recheck states, safe text rendering, read-only API, no-header isolation");
