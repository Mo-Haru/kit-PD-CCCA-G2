import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createContext, SourceTextModule, SyntheticModule } from "node:vm";

const source = await readFile(new URL("../app/static/js/watashi/auth-entry.mjs", import.meta.url), "utf8");
const settle = () => new Promise((resolve) => setImmediate(resolve));

function element(extra = {}) {
  const listeners = new Map();
  const classes = new Set();
  return {
    textContent: "", value: "", hidden: false, disabled: false, checked: false, dataset: {},
    validationMessage: "", listeners, classes,
    classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); } },
    addEventListener(type, listener) { listeners.set(type, listener); },
    setCustomValidity(message) { this.validationMessage = message; },
    emit(type) { return listeners.get(type)({ preventDefault() {} }); },
    ...extra,
  };
}

async function scenario(options = {}) {
  const page = element({ dataset: { returnUrl: options.returnUrl || "/family/invite?tab=received" } });
  const form = element();
  const fields = element({ disabled: true });
  const name = element({ value: "  表示名の検証  " });
  const consent = element();
  const register = element({ disabled: true, textContent: "同意してアカウントを作成" });
  const login = element({ disabled: true, textContent: "パスキーでログイン" });
  const status = element();
  const session = element();
  form.reportValidity = () => name.validationMessage === "" && consent.checked;
  const nodes = new Map([
    ["#auth-entry", page], ["#register-form", form], ["#register-fields", fields],
    ["#display-name", name], ["#privacy-consent", consent], ["#register", register],
    ["#login", login], ["#status", status], ["#session", session],
  ]);
  const calls = { api: [], register: [], login: 0 };
  const redirects = [], historyChanges = [];
  const location = {
    origin: "http://localhost", pathname: "/auth", search: "?next=%2Ffamily%2Finvite%3Ftab%3Dreceived",
    hash: options.fragment || "", assign(path) { redirects.push(path); },
  };
  const context = createContext({
    document: { querySelector(selector) { return nodes.get(selector); } },
    location, URL, URLSearchParams,
    history: { replaceState(_state, _title, path) { historyChanges.push(path); location.hash = ""; } },
  });
  const client = new SyntheticModule(["api", "login", "registerAccount"], function () {
    this.setExport("api", async (path) => {
      calls.api.push(path);
      if (options.sessionError) throw new Error("session unavailable");
      return { authenticated: options.authenticated === true };
    });
    this.setExport("login", async () => { calls.login += 1; });
    this.setExport("registerAccount", async (displayName) => {
      calls.register.push(displayName);
      if (options.registerError) throw options.registerError;
      if (options.register) await options.register();
    });
  }, { context });
  const entry = new SourceTextModule(source, { context });
  await entry.link((specifier) => {
    assert.equal(specifier, "./client.mjs");
    return client;
  });
  await entry.evaluate();
  await settle();
  return { form, fields, name, consent, register, login, status, session, calls, redirects, historyChanges, location };
}

const blocked = await scenario();
assert.equal(blocked.fields.disabled, false);
assert.equal(blocked.register.disabled, true);
assert.equal(blocked.login.disabled, false);
blocked.form.emit("submit");
await settle();
assert.equal(blocked.calls.register.length, 0);
// HTMLのrequiredを外しても、同意なしに登録用クライアントを呼ばない。
blocked.form.reportValidity = () => true;
blocked.form.emit("submit");
assert.equal(blocked.calls.register.length, 0);
assert.match(blocked.status.textContent, /同意/);
blocked.consent.checked = true;
blocked.consent.emit("change");
assert.equal(blocked.register.disabled, false);
blocked.consent.checked = false;
blocked.consent.emit("change");
assert.equal(blocked.register.disabled, true);
assert.equal(blocked.login.disabled, false);
blocked.login.emit("click");
await settle();
assert.equal(blocked.calls.login, 1);
assert.equal(blocked.calls.register.length, 0);
assert.deepEqual(blocked.redirects, ["/family/invite?tab=received"]);

let release;
const registering = await scenario({ register: () => new Promise((resolve) => { release = resolve; }) });
registering.consent.checked = true;
registering.consent.emit("change");
registering.form.emit("submit");
registering.form.emit("submit");
assert.deepEqual(registering.calls.register, ["表示名の検証"]);
assert.equal(registering.fields.disabled, true);
assert.equal(registering.login.disabled, true);
release();
await settle();
assert.deepEqual(registering.redirects, ["/family/invite?tab=received"]);

const cancelled = await scenario({ registerError: Object.assign(new Error("cancelled"), { name: "NotAllowedError" }) });
cancelled.consent.checked = true;
cancelled.consent.emit("change");
cancelled.form.emit("submit");
await settle();
assert.equal(cancelled.redirects.length, 0);
assert.equal(cancelled.register.disabled, false);
assert.equal(cancelled.consent.checked, true);
assert.equal(cancelled.name.value, "表示名の検証");
assert.equal(cancelled.status.classes.has("is-error"), true);
assert.match(cancelled.status.textContent, /操作が完了しませんでした/);

const restored = await scenario({ authenticated: true, fragment: "#return-fragment=token%3Dfixture-only" });
assert.deepEqual(restored.redirects, ["/family/invite?tab=received#token=fixture-only"]);
assert.deepEqual(restored.calls.api, ["/api/v1/auth/session"]);
assert.equal(restored.calls.login, 0);
assert.equal(restored.calls.register.length, 0);
assert.equal(restored.historyChanges.length, 1);
assert.equal(restored.historyChanges[0].includes("fixture-only"), false);
assert.equal(restored.location.hash, "");

const unavailable = await scenario({ sessionError: true });
assert.equal(unavailable.fields.disabled, true);
assert.equal(unavailable.register.disabled, true);
assert.equal(unavailable.login.disabled, true);
assert.equal(unavailable.status.classes.has("is-error"), true);
assert.equal(unavailable.redirects.length, 0);

const unsafe = await scenario({ returnUrl: "https://evil.example/path" });
unsafe.login.emit("click");
await settle();
assert.deepEqual(unsafe.redirects, ["/"]);
console.log("PASS: consent gating, independent login, duplicate registration prevention, cancellation, local redirects and browser-only fragment handoff");
