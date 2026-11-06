import { notify } from "./site-alert.mjs";
import { api, getUnlockState, openOwnedQrShare, unlock } from "./client.mjs";
import { bindQrShareDialog } from "./qr-share-dialog.mjs";
import { modeLabel } from "./sharing-flow.mjs";

// 共有の一覧の画面。共有リンクを作る手順は /sharing/new（sharing-new.mjs）。
// 暗号化された回答（発行済みの共有の中身）を開くときに、その場でパスキーの本人確認を求める。
export function bindSharingPage(root, client = { api, getUnlockState, openOwnedQrShare, unlock }) {
  if (!root.querySelector("#sharing-page")) return Promise.resolve();
  const node = id => root.querySelector(`#${id}`);
  let catalog = { questions: [], categories: [] };
  let session = null;
  let busy = false;
  let accountChanged = false;
  let decrypted = false;
  let detail = null;
  const stateLabel = { active: "有効", stopped: "停止", expired: "期限切れ", used: "使用済み" };

  function show(text, error = false) {
    notify(root, node("status"), text, error);
  }
  function isDecrypted() {
    return decrypted && !accountChanged && session?.authenticated && client.getUnlockState().unlocked;
  }
  function controls() {
    const loggedIn = Boolean(session?.authenticated);
    node("share-start").disabled = busy || accountChanged || !loggedIn;
    node("refresh-shares").disabled = busy || accountChanged || !loggedIn;
    node("login-here").hidden = !session || loggedIn;
    node("lock-state").textContent = accountChanged ? "アカウントが変わりました。ページを開き直してください。"
      : !session ? "ログイン状態を確認しています。"
        : !loggedIn ? "ログインすると、共有リンクを作れます。"
          : `${session.display_name} さん：暗号化された回答を開くときに、パスキーで本人確認をします。`;
    for (const button of node("qr-shares").querySelectorAll("button")) button.disabled = busy || accountChanged;
  }
  async function readSession() {
    if (accountChanged) throw new Error("アカウントが変わりました。ページを開き直してください。");
    const current = await client.api("/api/v1/auth/session");
    if (session && current.user_id !== session.user_id) {
      accountChanged = true;
      decrypted = false;
      detail?.clear();
      node("qr-shares").replaceChildren();
      controls();
      throw new Error("アカウントが変わりました。ページを開き直してください。");
    }
    session = current;
    return current;
  }
  async function refreshShares() {
    const result = await client.api("/api/v1/qr-shares");
    const rows = result.shares.map(share => {
      const row = root.createElement("li");
      row.className = "record-card";
      const info = root.createElement("button");
      info.type = "button";
      info.className = "sharing-issued-card";
      info.dataset.shareState = share.status;
      const summary = root.createElement("span");
      summary.className = "sharing-issued-summary";
      summary.textContent = `${modeLabel(share.mode)} · ${stateLabel[share.status] || share.status}\n発行 ${share.created_at}\n期限 ${share.expires_at || "なし"}`;
      const action = root.createElement("span");
      action.className = "sharing-issued-open";
      action.textContent = "開く";
      info.append(summary, action);
      info.addEventListener("click", () => detail.open(share.id, info));
      row.append(info);
      return row;
    });
    node("qr-shares").replaceChildren(...rows);
    if (!rows.length) {
      const row = root.createElement("li");
      row.textContent = "発行済みの共有はありません。「共有を開始」から作れます。";
      node("qr-shares").append(row);
    }
    controls();
  }
  function handleError(error) {
    if (error.code === "reauth_required" || error.status === 401) decrypted = false;
    const message = ["NotAllowedError", "AbortError"].includes(error.name)
      ? "本人確認を完了できませんでした。もう一度お試しください。"
      : error.code === "reauth_required" ? "本人確認の期限が切れました。もう一度、パスキーで本人確認をしてください。"
        : error.message || "操作できませんでした。";
    show(message, true);
    controls();
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    controls();
    try { await action(); }
    catch (error) { handleError(error); }
    finally { busy = false; controls(); }
  }
  // 暗号化された回答を開くときに呼ばれる。パスキーで本人確認をして、鍵を解除する。
  async function unlockAnswers() {
    if (isDecrypted()) return;
    await client.unlock();
    await readSession();
    decrypted = true;
  }
  detail = bindQrShareDialog(root, { client, readSession, canDecrypt: isDecrypted, unlockAnswers,
    catalog: () => catalog, modeLabel, onStopped: refreshShares, onError: handleError });
  node("share-start").addEventListener("click", () => globalThis.location.assign("/sharing/new"));
  node("refresh-shares").addEventListener("click", () => run(async () => { await readSession(); await refreshShares(); }));
  return run(async () => {
    await readSession();
    catalog = await client.api("/api/v1/answers/questions");
    if (session.authenticated) await refreshShares();
  });
}

if (typeof document !== "undefined" && document.querySelector("#sharing-page")) bindSharingPage(document);
