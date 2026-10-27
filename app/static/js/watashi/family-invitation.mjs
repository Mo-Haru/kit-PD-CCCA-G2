import { api } from "./client.mjs";

export async function bindFamilyInvitation(root, call = api, address = location, navigation = history) {
  if (!root.querySelector("#family-invitation")) return;
  const node = id => root.querySelector(`#${id}`);
  const token = new URLSearchParams(address.hash.slice(1)).get("token");
  const accept = node("accept-invitation");
  let busy = false;
  let accepted = false;
  let accountId = null;
  let authenticated = false;
  function show(text) { node("invite-status").textContent = text; }
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) {
    node("invite-session").textContent = "招待リンクを確認できません。";
    show("届いた招待リンクまたはQRコードから、もう一度開いてください。");
    return;
  }
  const login = new URL("/auth", address.origin);
  login.searchParams.set("next", "/family/invite");
  login.hash = new URLSearchParams({ "return-fragment": `token=${token}` }).toString();
  node("invite-login").href = login.pathname + login.search + login.hash;
  async function session() {
    const state = await call("/api/v1/auth/session");
    authenticated = state.authenticated;
    node("invite-session").textContent = state.authenticated ? `${state.display_name} でログイン中` : "ログインしていません。";
    node("invite-guest").hidden = state.authenticated;
    node("invite-confirmation").hidden = !state.authenticated || accepted;
    accept.disabled = !state.authenticated || busy || accepted;
    return state;
  }
  accept.addEventListener("click", async () => {
    if (busy || accepted) return;
    busy = true;
    accept.disabled = true;
    try {
      const state = await session();
      if (!state.authenticated) return;
      if (state.user_id !== accountId) {
        accountId = state.user_id;
        show("ログイン中のアカウントが変わりました。表示名を確認して、もう一度承諾してください。");
        return;
      }
      await call("/api/v1/family/invitations/request", { method: "POST", body: { token } });
      accepted = true;
      navigation.replaceState(null, "", address.pathname);
      node("invite-confirmation").hidden = true;
      show("招待を承諾しました。招待した方の確認をお待ちください。");
    } catch (error) { show(error.message || "招待を承諾できませんでした。"); }
    finally { busy = false; accept.disabled = accepted || !authenticated; }
  });
  try { const state = await session(); accountId = state.user_id; }
  catch (_error) { show("ログイン状態を確認できません。ページを開き直してください。"); }
}

if (typeof document !== "undefined") bindFamilyInvitation(document);
