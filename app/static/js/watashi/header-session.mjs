import { api } from "./client.mjs";

const account = document.querySelector("#header-account");

if (account) {
  const link = document.querySelector("#header-account-link");
  const accounts = [account, document.querySelector("#menu-account")].filter(Boolean);
  const labels = ["#header-session-label", "#menu-session-label"].map((selector) => document.querySelector(selector)).filter(Boolean);
  const names = ["#header-session-name", "#menu-session-name"].map((selector) => document.querySelector(selector)).filter(Boolean);
  const links = [link, document.querySelector("#menu-account-link")].filter(Boolean);
  const loginUrl = link.href;
  const accountUrl = link.dataset.accountUrl;
  let refreshing = false;

  function loginHref() {
    const url = new URL(loginUrl, location.origin);
    if (location.hash) {
      url.hash = new URLSearchParams({ "return-fragment": location.hash.slice(1) }).toString();
    }
    return url.pathname + url.search + url.hash;
  }

  function setDestination(authenticated) {
    const href = authenticated ? accountUrl : loginHref();
    for (const anchor of document.querySelectorAll("[data-auth-link]")) anchor.href = href;
  }

  setDestination(false);

  function showSession(state, displayName = "") {
    const authenticated = state === "authenticated";
    const failed = state === "error";
    for (const container of accounts) container.dataset.sessionState = state;
    for (const label of labels) {
      label.textContent = failed ? "ログイン状態を確認できません" : authenticated ? "ログイン中" : "未ログイン";
    }
    for (const name of names) {
      name.textContent = authenticated ? `${displayName} さん` : "";
      name.hidden = !authenticated;
    }
    for (const anchor of links) {
      anchor.textContent = failed ? "ログイン・アカウント" : authenticated ? "アカウント" : "ログイン・新規登録";
    }
    setDestination(authenticated);
  }

  async function refreshSession() {
    if (refreshing) return;
    refreshing = true;
    for (const container of accounts) container.setAttribute("aria-busy", "true");
    try {
      const session = await api("/api/v1/auth/session");
      if (typeof session.authenticated !== "boolean") throw new Error("Invalid session response");
      showSession(session.authenticated ? "authenticated" : "guest", session.display_name);
    } catch (_error) {
      showSession("error");
    } finally {
      refreshing = false;
      for (const container of accounts) container.setAttribute("aria-busy", "false");
    }
  }

  window.addEventListener("pageshow", (event) => {
    if (event.persisted) refreshSession();
  });
  window.addEventListener("focus", refreshSession);
  window.addEventListener("hashchange", () => {
    if (account.dataset.sessionState !== "authenticated") setDestination(false);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshSession();
  });
  refreshSession();
}
