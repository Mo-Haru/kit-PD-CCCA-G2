import { entriesFromPayload, fillAnswerList } from "./crypto.mjs";

export function bindQrShareDialog(root, { client, readSession, canDecrypt, unlockAnswers, catalog, modeLabel, onStopped, onError }) {
  const node = id => root.querySelector(`#${id}`);
  const dialog = node("share-detail");
  let share = null;
  let sequence = 0;
  let busy = false;
  let trigger = null;
  const stop = node("share-stop");
  const states = { active: "有効", stopped: "停止済み", expired: "期限切れ", used: "使用済み" };

  function sync() {
    stop.disabled = busy || !share || share.status === "stopped";
    stop.textContent = share?.status === "stopped" ? "共有停止済み" : "共有停止";
    node("share-detail-unlock").disabled = busy;
  }
  function message(text) { node("share-detail-status").textContent = text; }
  async function render(ticket) {
    node("share-detail-answers").replaceChildren();
    node("share-detail-cipher").hidden = true;
    node("share-detail-cipher").textContent = "";
    node("share-detail-unlock").hidden = true;
    if (!canDecrypt()) {
      // 暗号化された回答を開くので、ここでパスキーの本人確認を求める。
      message("暗号化された回答を開くため、パスキーで本人確認をします…");
      try { await unlockAnswers(); }
      catch (error) {
        if (ticket !== sequence || !dialog.open) return;
        node("share-detail-cipher").textContent = `${share.version.ciphertext.slice(0, 128)}…`;
        node("share-detail-cipher").hidden = false;
        node("share-detail-unlock").hidden = false;
        message(["NotAllowedError", "AbortError"].includes(error.name)
          ? "本人確認を完了できませんでした。もう一度お試しください。" : error.message || "本人確認を完了できませんでした。");
        return;
      }
      if (ticket !== sequence || !dialog.open) return;
    }
    try {
      const payload = await client.openOwnedQrShare(share);
      if (ticket !== sequence || !dialog.open) return;
      fillAnswerList(node("share-detail-answers"), entriesFromPayload(payload, catalog()));
      message(share.status === "stopped" ? "この共有は停止済みです。発行時点の回答を表示しています。" : "発行時点の回答を復号して表示しています。");
    } catch (error) {
      if (ticket !== sequence || !dialog.open) return;
      message(error.code === "owner_copy_unavailable" ? error.message : "共有内容を復号できませんでした。回答は表示していません。");
    }
  }
  async function load(id) {
    const ticket = ++sequence;
    message("共有内容を読み込んでいます…");
    node("share-detail-answers").replaceChildren();
    node("share-detail-cipher").hidden = true;
    node("share-detail-unlock").hidden = true;
    await readSession();
    const result = await client.api(`/api/v1/qr-shares/${id}`);
    if (ticket !== sequence || !dialog.open) return;
    share = result.share;
    node("share-detail-facts").textContent = `${modeLabel(share.mode)} / ${states[share.status] || share.status} / 発行 ${share.created_at} / 期限 ${share.expires_at || "なし"} / 版 ${share.current_version}`;
    await render(ticket);
  }
  async function act(action) {
    if (busy) return;
    busy = true;
    sync();
    try { await action(); }
    catch (error) {
      onError(error);
      if (dialog.open) {
        node("share-detail-unlock").hidden = canDecrypt();
        if (!canDecrypt()) {
          node("share-detail-answers").replaceChildren();
          node("share-detail-cipher").textContent = share ? `${share.version.ciphertext.slice(0, 128)}…` : "";
          node("share-detail-cipher").hidden = !share;
        }
        message(error.message || "操作できませんでした。");
      }
    } finally { busy = false; sync(); }
  }
  function close() {
    sequence += 1;
    share = null;
    node("share-detail-answers").replaceChildren();
    node("share-detail-cipher").textContent = "";
    node("share-detail-facts").textContent = "";
    if (dialog.open) dialog.close();
    if (trigger?.isConnected === false) node("refresh-shares").focus();
    else trigger?.focus();
    trigger = null;
  }
  node("share-close").addEventListener("click", close);
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
  dialog.addEventListener("close", () => {
    sequence += 1;
    node("share-detail-answers").replaceChildren();
    node("share-detail-cipher").textContent = "";
  });
  stop.addEventListener("click", () => act(async () => {
    if (!share || share.status === "stopped") return;
    if (!globalThis.confirm("この共有を停止しますか？\n停止すると、このリンクから回答を閲覧できなくなります。相手が保存済みの回答は削除されません。")) return;
    const id = share.id;
    await readSession();
    await client.api(`/api/v1/qr-shares/${id}/stop`, { method: "POST", body: {} });
    // Keep a confirmed stop visible even if refreshing the list fails.
    if (share?.id === id) share.status = "stopped";
    sync();
    message("共有を停止しました。次回の取得から閲覧できなくなります。");
    await onStopped();
    if (dialog.open) await load(id);
  }));
  node("share-detail-unlock").addEventListener("click", () => act(async () => {
    await unlockAnswers();
    if (share && dialog.open) await load(share.id);
  }));
  return {
    open(id, opener) {
      if (dialog.open || busy) return;
      trigger = opener;
      share = null;
      dialog.showModal();
      return act(() => load(id));
    },
    clear: close,
  };
}
