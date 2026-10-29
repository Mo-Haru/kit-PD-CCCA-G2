import { api, unlock } from "./client.mjs";

const MESSAGES = {
  not_found: "この確認リンクでは登録できません。招待したアカウントでログインしているか、最新の確認リンクかを確認してください。",
  invitation_invalid: "この申請はすでに登録済み、取り消し、または期限切れです。",
  confirmation_required: "この申請には確認リンクがありません。申請した家族に再発行してもらってください。",
  already_connected: "すでに家族として登録されています。",
};

export async function bindFamilyConfirm(root, { call = api, reauth = unlock, address = location, navigation = history } = {}) {
  if (!root.querySelector("#family-confirm")) return;
  const node = id => root.querySelector(`#${id}`);
  const token = new URLSearchParams(address.hash.slice(1)).get("token");
  const button = node("confirm-link");
  let busy = false;
  let done = false;
  let accountId = null;
  function show(text) { node("confirm-status").textContent = text; }
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) {
    node("confirm-session").textContent = "確認リンクを確認できません。";
    show("家族から受け取った確認リンクまたはQRコードから、もう一度開いてください。");
    return;
  }
  const login = new URL("/auth", address.origin);
  login.searchParams.set("next", "/family/confirm");
  login.hash = new URLSearchParams({ "return-fragment": `token=${token}` }).toString();
  node("confirm-login").href = login.pathname + login.search + login.hash;
  async function session() {
    const state = await call("/api/v1/auth/session");
    node("confirm-session").textContent = state.authenticated ? `${state.display_name} でログイン中` : "ログインしていません。";
    node("confirm-guest").hidden = state.authenticated;
    node("confirm-action").hidden = !state.authenticated || done;
    button.disabled = !state.authenticated || busy || done;
    return state;
  }
  button.addEventListener("click", async () => {
    if (busy || done) return;
    busy = true;
    button.disabled = true;
    try {
      const state = await session();
      if (!state.authenticated) return;
      if (state.user_id !== accountId) {
        accountId = state.user_id;
        show("ログイン中のアカウントが変わりました。表示名を確認して、もう一度押してください。");
        return;
      }
      show("パスキーで本人確認をしています…");
      await reauth();
      const result = await call("/api/v1/family/invitations/confirm-link", { method: "POST", body: { token } });
      done = true;
      navigation.replaceState(null, "", address.pathname);
      node("confirm-action").hidden = true;
      show(`${result.recipient_display_name || "家族"} さんを家族に追加しました。`);
    } catch (error) {
      show(["NotAllowedError", "AbortError"].includes(error.name)
        ? "本人確認を完了できませんでした。もう一度お試しください。"
        : MESSAGES[error.code] || error.message || "家族に追加できませんでした。");
    } finally {
      busy = false;
      button.disabled = done || accountId === null;
    }
  });
  try {
    const state = await session();
    if (state.authenticated) accountId = state.user_id;
  } catch (_error) {
    show("ログイン状態を確認できません。ページを開き直してください。");
  }
}

if (typeof document !== "undefined") bindFamilyConfirm(document);
