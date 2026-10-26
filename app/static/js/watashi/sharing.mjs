import { api, createQrShare, getUnlockState, loadAnswerDocument, unlock } from "./client.mjs";
import { entriesFromPayload, fillAnswerList, plaintextForSelection } from "./crypto.mjs";

export function qrConfirmationKey(selection, mode, expiresAt, ownerId) {
  return JSON.stringify({ selection, mode, expiresAt, ownerId });
}

export function bindSharingPage(root, client = { api, createQrShare, getUnlockState, loadAnswerDocument, unlock }) {
  if (!root.querySelector("#sharing-page")) return;
  const node = id => root.querySelector(`#${id}`);
  let catalog = { questions: [], categories: [] };
  let session = null;
  let preview = null;
  let generation = 0;
  let busy = false;
  let accountChanged = false;
  const stateLabel = { active: "有効", stopped: "停止", expired: "期限切れ", used: "使用済み" };

  function show(text, error = false) {
    node("status").textContent = text;
    node("status").classList.toggle("is-error", error);
  }
  function controls() {
    node("share-fields").disabled = busy || accountChanged || !session?.authenticated || !client.getUnlockState().unlocked;
    node("unlock-here").disabled = busy || accountChanged;
    node("refresh-shares").disabled = busy || accountChanged || !session?.authenticated;
    node("confirm-share").disabled = !preview;
    node("create-qr").disabled = busy || !preview || !node("confirm-share").checked;
    for (const button of node("qr-shares").querySelectorAll("button")) button.disabled = busy || accountChanged;
  }
  function clearPreview() {
    generation += 1;
    preview = null;
    node("confirm-share").checked = false;
    node("preview-output").replaceChildren();
    controls();
  }
  function selection() {
    const scope = root.querySelector("input[name=scope]:checked").value;
    if (scope === "all") return { scope };
    const group = scope === "chapters" ? "chapters" : "questions";
    return { scope, ids: [...node(group).querySelectorAll("input:checked")].map(input => input.value) };
  }
  function settings() {
    const mode = root.querySelector("input[name=mode]:checked").value;
    let expiresAt = null;
    if (mode === "timed") {
      const date = new Date(node("expires-at").value);
      if (!Number.isFinite(date.getTime()) || date <= new Date() || date > new Date(Date.now() + 31 * 86400000)) {
        throw new Error("有効期限を現在より後、31日以内に指定してください。");
      }
      expiresAt = date.toISOString();
    }
    return { selection: selection(), mode, expiresAt };
  }
  function visibility() {
    const scope = root.querySelector("input[name=scope]:checked").value;
    node("chapters").hidden = scope !== "chapters";
    node("questions").hidden = scope !== "questions";
    const timed = root.querySelector("input[name=mode]:checked").value === "timed";
    node("expiry-field").hidden = !timed;
    node("expires-at").disabled = !timed;
  }
  function choice(parent, id, text) {
    const label = root.createElement("label");
    const input = root.createElement("input");
    input.type = "checkbox";
    input.value = id;
    label.append(input, root.createTextNode(` ${text}`));
    parent.append(label);
  }
  async function readSession() {
    if (accountChanged) throw new Error("アカウントが変わりました。ページを開き直してください。");
    const current = await client.api("/api/v1/auth/session");
    if (session && current.user_id !== session.user_id) {
      accountChanged = true;
      clearPreview();
      node("qr-output").hidden = true;
      node("share-url").value = "";
      node("open-link").removeAttribute("href");
      node("qr-shares").replaceChildren();
      throw new Error("アカウントが変わりました。ページを開き直してください。");
    }
    session = current;
    node("login-here").hidden = current.authenticated;
    node("unlock-here").hidden = !current.authenticated;
    node("lock-state").textContent = !current.authenticated ? "ログイン後、この画面で鍵を解除してください。"
      : client.getUnlockState().unlocked ? `${current.display_name} さん：鍵は解除済みです。`
        : `${current.display_name} さん：この画面で鍵を解除してください。`;
    return current;
  }
  async function refreshShares() {
    const result = await client.api("/api/v1/qr-shares");
    const rows = result.shares.map(share => {
      const row = root.createElement("li");
      row.className = "record-card";
      const info = root.createElement("p");
      info.textContent = `${share.mode === "timed" ? "期限付き" : "緊急用"} / ${stateLabel[share.status] || share.status} / ${share.created_at} / 期限 ${share.expires_at || "なし"}`;
      row.append(info);
      if (share.status !== "stopped") {
        const stop = root.createElement("button");
        stop.type = "button";
        stop.textContent = "この共有を停止";
        stop.addEventListener("click", () => run(async () => {
          await readSession();
          await client.api(`/api/v1/qr-shares/${share.id}/stop`, { method: "POST", body: {} });
          show("共有を停止しました。次回の取得から閲覧できなくなります。");
          await refreshShares();
        }));
        row.append(stop);
      }
      return row;
    });
    node("qr-shares").replaceChildren(...rows);
    if (!rows.length) {
      const row = root.createElement("li");
      row.textContent = "発行済みの共有はありません。";
      node("qr-shares").append(row);
    }
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    controls();
    try { await action(); }
    catch (error) { show(error.message || "操作できませんでした。", true); }
    finally { busy = false; controls(); }
  }
  node("share-fields").addEventListener("change", event => {
    if (event.target === node("confirm-share")) { controls(); return; }
    clearPreview();
    visibility();
  });
  node("expires-at").addEventListener("input", clearPreview);
  node("unlock-here").addEventListener("click", () => run(async () => {
    await client.unlock();
    await readSession();
    show("鍵を解除しました。共有する範囲を選んでください。");
  }));
  node("show-preview").addEventListener("click", () => run(async () => {
    clearPreview();
    await readSession();
    const version = generation;
    const chosen = settings();
    const loaded = await client.loadAnswerDocument();
    if (loaded.revision === 0) throw new Error("保存済みの回答がありません。先に回答画面でDBへ保存してください。");
    const payload = plaintextForSelection(loaded.document, catalog, chosen.selection);
    if (generation !== version) throw new Error("設定が変わったため、もう一度内容を確認してください。");
    preview = { ...chosen, payload, key: qrConfirmationKey(chosen.selection, chosen.mode, chosen.expiresAt, session.user_id) };
    const facts = root.createElement("p");
    facts.textContent = `${chosen.mode === "timed" ? "期限付き" : "緊急用（最初の閲覧のみ）"} / 期限 ${chosen.expiresAt ? new Date(chosen.expiresAt).toLocaleString() : "なし"}`;
    const answers = root.createElement("div");
    fillAnswerList(answers, entriesFromPayload(payload, catalog));
    node("preview-output").replaceChildren(facts, answers);
    show("内容と期限を確認して、確認欄にチェックしてください。");
  }));
  node("create-qr").addEventListener("click", () => run(async () => {
    await readSession();
    const chosen = settings();
    if (!preview || !node("confirm-share").checked
      || preview.key !== qrConfirmationKey(chosen.selection, chosen.mode, chosen.expiresAt, session.user_id)) {
      clearPreview();
      throw new Error("共有する内容をもう一度確認してください。");
    }
    const created = await client.createQrShare({ ...chosen, payload: structuredClone(preview.payload) });
    node("share-url").value = created.url;
    node("open-link").href = created.url;
    node("link-facts").textContent = `作成日時 ${created.share.created_at} / ${created.share.mode === "timed" ? "期限付き" : "緊急用"}`;
    node("qr-output").hidden = false;
    clearPreview();
    show("共有リンクを作成しました。開くだけでは閲覧を開始せず、「内容を開く」で開始します。");
    try { await refreshShares(); }
    catch (_error) { show("共有リンクは作成済みです。一覧を更新できませんでした。表示中のリンクをコピーしてください。", true); }
  }));
  node("copy-link").addEventListener("click", () => run(async () => {
    await navigator.clipboard.writeText(node("share-url").value);
    show("共有リンクをコピーしました。");
  }));
  node("refresh-shares").addEventListener("click", () => run(async () => { await readSession(); await refreshShares(); }));
  visibility();
  return run(async () => {
    await readSession();
    catalog = await client.api("/api/v1/answers/questions");
    for (const category of catalog.categories) choice(node("chapters"), category.id, category.name);
    for (const question of catalog.questions) choice(node("questions"), question.id, question.text);
    if (session.authenticated) await refreshShares();
  });
}

if (typeof document !== "undefined") bindSharingPage(document);
