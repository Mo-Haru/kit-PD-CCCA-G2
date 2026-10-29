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
  const readToken = () => new URLSearchParams(address.hash.slice(1)).get("token");
  const token = readToken();
  const button = node("confirm-link");
  let busy = false;
  let done = false;
  let stale = false;
  let accountId = null;
  function show(text) { node("confirm-status").textContent = text; }
  // 同じタブで別の確認リンクを開くと、ページは残ったままfragmentだけが変わる。
  // 最初のリンクの申請者を承認しないよう、変わった時点で操作を止めて再読み込みを求める。
  function tokenStillMatches() {
    if (!stale && !done && readToken() !== token) {
      stale = true;
      button.disabled = true;
      node("confirm-guest").hidden = true;
      node("confirm-action").hidden = true;
      node("confirm-session").textContent = "確認リンクが変更されました。";
      show("新しい確認リンクを確認するため、ページを再読み込みしてください。");
    }
    return !stale;
  }
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) {
    node("confirm-session").textContent = "確認リンクを確認できません。";
    show("家族から受け取った確認リンクまたはQRコードから、もう一度開いてください。");
    return;
  }
  const login = new URL("/auth", address.origin);
  login.searchParams.set("next", "/family/confirm");
  login.hash = new URLSearchParams({ "return-fragment": `token=${token}` }).toString();
  node("confirm-login").href = login.pathname + login.search + login.hash;
  root.defaultView?.addEventListener("hashchange", tokenStillMatches);
  async function session() {
    const state = await call("/api/v1/auth/session");
    if (!tokenStillMatches()) return null;
    node("confirm-session").textContent = state.authenticated ? `${state.display_name} でログイン中` : "ログインしていません。";
    node("confirm-guest").hidden = state.authenticated;
    node("confirm-action").hidden = !state.authenticated || done;
    button.disabled = !state.authenticated || busy || done;
    return state;
  }
  button.addEventListener("click", async () => {
    if (busy || done || !tokenStillMatches()) return;
    busy = true;
    button.disabled = true;
    try {
      const state = await session();
      if (!state?.authenticated) return;
      if (state.user_id !== accountId) {
        accountId = state.user_id;
        show("ログイン中のアカウントが変わりました。表示名を確認して、もう一度押してください。");
        return;
      }
      show("パスキーで本人確認をしています…");
      await reauth();
      if (!tokenStillMatches()) return;
      const result = await call("/api/v1/family/invitations/confirm-link", { method: "POST", body: { token } });
      if (!tokenStillMatches()) {
        // 送信済みの承認は取り消せない。どの申請を承認したかを伝え、新しいリンクは読み込み直してもらう。
        show(`変更前の確認リンクで ${result.recipient_display_name || "家族"} さんを家族に追加しました。新しい確認リンクは、ページを再読み込みしてから確認してください。`);
        return;
      }
      done = true;
      navigation.replaceState(null, "", address.pathname);
      node("confirm-action").hidden = true;
      show(`${result.recipient_display_name || "家族"} さんを家族に追加しました。`);
    } catch (error) {
      if (!tokenStillMatches()) return;
      show(["NotAllowedError", "AbortError"].includes(error.name)
        ? "本人確認を完了できませんでした。もう一度お試しください。"
        : MESSAGES[error.code] || error.message || "家族に追加できませんでした。");
    } finally {
      busy = false;
      button.disabled = done || stale || accountId === null;
    }
  });
  try {
    const state = await session();
    if (state?.authenticated) accountId = state.user_id;
  } catch (_error) {
    if (tokenStillMatches()) show("ログイン状態を確認できません。ページを開き直してください。");
  }
}

if (typeof document !== "undefined") bindFamilyConfirm(document);
