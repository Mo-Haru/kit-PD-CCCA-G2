import { api, login, registerAccount } from "./client.mjs";

const page = document.querySelector("#auth-entry");
const form = document.querySelector("#register-form");
const fields = document.querySelector("#register-fields");
const displayName = document.querySelector("#display-name");
const consent = document.querySelector("#privacy-consent");
const registerButton = document.querySelector("#register");
const loginButton = document.querySelector("#login");
const status = document.querySelector("#status");
const sessionLabel = document.querySelector("#session");
let busy = false;
let sessionKnown = false;

const destination = new URL(page.dataset.returnUrl, location.origin);
if (destination.origin !== location.origin) destination.href = new URL("/", location.origin).href;
// 招待トークン等の断片は、nextのクエリへ混ぜずブラウザ内だけで引き継ぐ。
const fragment = new URLSearchParams(location.hash.slice(1)).get("return-fragment");
if (fragment !== null) {
  destination.hash = fragment;
  history.replaceState(null, "", location.pathname + location.search);
}

function returnToPage() {
  location.assign(destination.pathname + destination.search + destination.hash);
}

function show(text, isError = false) {
  status.textContent = text;
  status.classList.toggle("is-error", isError);
}

function syncControls() {
  fields.disabled = busy || !sessionKnown;
  registerButton.disabled = busy || !sessionKnown || !consent.checked;
  loginButton.disabled = busy || !sessionKnown;
}

async function authenticate(button, pending, action) {
  if (busy || !sessionKnown) return;
  busy = true;
  const label = button.textContent;
  button.textContent = pending;
  syncControls();
  show(pending);
  let completed = false;
  try {
    await action();
    completed = true;
    show("手続きが完了しました。元のページに戻ります。");
    returnToPage();
  } catch (error) {
    const message = error.name === "NotAllowedError" || error.name === "AbortError"
      ? "パスキーの操作が完了しませんでした。端末の案内を確認し、もう一度お試しください。"
      : error.message || "操作できませんでした。もう一度お試しください。";
    show(completed ? "手続きは完了しましたが、ページに戻れませんでした。ページを再読み込みしてください。" : message, true);
    button.textContent = label;
    busy = false;
    syncControls();
  }
}

consent.addEventListener("change", syncControls);
displayName.addEventListener("input", () => displayName.setCustomValidity(""));
form.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = displayName.value.trim();
  displayName.setCustomValidity(name ? "" : "表示名を入力してください。");
  if (!form.reportValidity()) return;
  if (!consent.checked) {
    show("注意事項を確認し、同意にチェックしてください。", true);
    return;
  }
  displayName.value = name;
  authenticate(registerButton, "パスキーを登録しています…", () => registerAccount(name));
});
loginButton.addEventListener("click", () => authenticate(loginButton, "ログインしています…", login));

async function checkSession() {
  syncControls();
  const session = await api("/api/v1/auth/session");
  if (session.authenticated) {
    sessionLabel.textContent = "ログイン済みです。元のページに戻ります。";
    returnToPage();
    return;
  }
  sessionLabel.hidden = true;
  sessionKnown = true;
  syncControls();
}

checkSession().catch(() => show("ログイン状態を確認できませんでした。接続を確認して、ページを再読み込みしてください。", true));
