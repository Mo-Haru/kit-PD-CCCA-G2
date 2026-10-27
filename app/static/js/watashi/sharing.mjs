import { api, createQrShare, getUnlockState, loadAnswerDocument, openOwnedQrShare, unlock } from "./client.mjs";
import { entriesFromPayload, fillAnswerList, plaintextForSelection, presentAnswer } from "./crypto.mjs";
import { bindQrShareDialog } from "./qr-share-dialog.mjs";

const modeLabel = mode => ({ emergency: "緊急用（最初の閲覧のみ）", timed: "期限付き", unlimited: "無期限・複数回" }[mode] || mode);

export function qrConfirmationKey(selection, mode, expiresAt, ownerId) {
  return JSON.stringify({ selection, mode, expiresAt, ownerId });
}

export function bindSharingPage(root, client = { api, createQrShare, getUnlockState, loadAnswerDocument, openOwnedQrShare, unlock }) {
  if (!root.querySelector("#sharing-page")) return;
  const node = id => root.querySelector(`#${id}`);
  let catalog = { questions: [], categories: [] };
  let session = null;
  let preview = null;
  let generation = 0;
  let busy = false;
  let accountChanged = false;
  let decrypted = false;
  let unlocking = false;
  let catalogReady = false;
  let encrypted = null;
  let answerDocument = null;
  let detail = null;
  const answerViews = [];
  const stateLabel = { active: "有効", stopped: "停止", expired: "期限切れ", used: "使用済み" };

  function show(text, error = false) {
    node("status").textContent = text;
    node("status").classList.toggle("is-error", error);
  }
  function isDecrypted() {
    return decrypted && !accountChanged && session?.authenticated && client.getUnlockState().unlocked;
  }
  function canPreview() {
    if (!isDecrypted()) return false;
    try {
      const chosen = settings();
      return chosen.selection.scope === "all" ? catalog.questions.length > 0 : chosen.selection.ids.length > 0;
    } catch (_error) { return false; }
  }
  function controls() {
    const unlocked = Boolean(isDecrypted());
    node("share-fields").disabled = busy || unlocking || accountChanged || !session?.authenticated || !catalogReady;
    const unlockButton = node("unlock-here");
    unlockButton.disabled = busy || unlocking || accountChanged || unlocked;
    unlockButton.dataset.unlockState = unlocked ? "unlocked" : "locked";
    unlockButton.textContent = unlocked ? "解除済み" : unlocking ? "解除中…" : "回答の暗号化を解除";
    unlockButton.setAttribute("aria-busy", String(unlocking));
    node("sharing-unlock-help").hidden = unlocked || !session?.authenticated;
    node("lock-state").textContent = accountChanged ? "アカウントが変わりました。ページを開き直してください。"
      : !session ? "ログイン状態を確認しています。"
        : !session.authenticated ? "ログインすると、回答の暗号化を解除できます。"
          : unlocked ? `${session.display_name} さん：解除済み。回答内容を確認して共有できます。`
            : `${session.display_name} さん：回答は暗号化されています。内容の確認と共有には解除が必要です。`;
    node("show-preview").disabled = busy || !canPreview();
    node("refresh-shares").disabled = busy || accountChanged || !session?.authenticated;
    node("confirm-share").disabled = !preview;
    node("create-qr").disabled = busy || !unlocked || !preview || !node("confirm-share").checked;
    for (const button of node("qr-shares").querySelectorAll("button")) button.disabled = busy || accountChanged;
    renderAnswers();
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
    node("all-answers").hidden = scope !== "all";
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
    return label;
  }
  function answerView(parent, question, includeTitle = true) {
    const row = root.createElement("span");
    row.className = "sharing-answer-row";
    if (includeTitle) {
      const title = root.createElement("span");
      title.className = "sharing-question-title";
      title.textContent = question.text;
      row.append(title);
    }
    const value = root.createElement("span");
    value.className = "sharing-answer-value";
    answerViews.push({ id: question.id, node: value });
    row.append(value);
    parent.append(row);
  }
  function renderAnswers() {
    const unlocked = Boolean(isDecrypted());
    for (const view of answerViews) {
      view.node.dataset.answerState = unlocked ? "decrypted" : "encrypted";
      if (unlocked) {
        const answer = presentAnswer(answerDocument?.items?.[view.id]);
        view.node.textContent = answer.stateLabel === "回答" ? `回答：${answer.valueText || "（空欄）"}` : answer.stateLabel;
      } else {
        view.node.textContent = !session?.authenticated ? "ログインして回答を確認"
          : encrypted?.document === null ? "保存済みの回答はありません"
            : encrypted ? `暗号化中：${encrypted.document.ciphertext.slice(0, 64)}…` : "回答データを確認しています…";
      }
    }
  }
  function renderCatalog() {
    for (const category of catalog.categories) {
      const group = root.createElement("div");
      group.className = "sharing-chapter";
      choice(group, category.id, category.name);
      for (const question of catalog.questions.filter(question => question.category_id === category.id)) answerView(group, question);
      node("chapters").append(group);
    }
    for (const question of catalog.questions) {
      const label = choice(node("questions"), question.id, "");
      answerView(label, question);
      answerView(node("all-answers"), question);
    }
    catalogReady = true;
  }
  async function readSession() {
    if (accountChanged) throw new Error("アカウントが変わりました。ページを開き直してください。");
    const current = await client.api("/api/v1/auth/session");
    if (session && current.user_id !== session.user_id) {
      accountChanged = true;
      decrypted = false;
      answerDocument = null;
      encrypted = null;
      detail?.clear();
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
      row.textContent = "発行済みの共有はありません。";
      node("qr-shares").append(row);
    }
  }
  function handleError(error) {
    if (error.code === "reauth_required" || error.status === 401) {
      decrypted = false;
      answerDocument = null;
      clearPreview();
    }
    const message = ["NotAllowedError", "AbortError"].includes(error.name)
      ? "暗号化の解除を完了できませんでした。もう一度お試しください。"
      : error.code === "reauth_required" ? "本人確認の期限が切れました。「回答の暗号化を解除」をもう一度押してください。"
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
  node("share-fields").addEventListener("change", event => {
    if (event.target === node("confirm-share")) { controls(); return; }
    clearPreview();
    visibility();
  });
  node("expires-at").addEventListener("input", clearPreview);
  async function unlockAnswers() {
    if (isDecrypted()) return;
    unlocking = true;
    controls();
    show("回答の暗号化を解除しています…");
    try {
      await client.unlock();
      await readSession();
      const loaded = await client.loadAnswerDocument();
      answerDocument = loaded.document;
      decrypted = true;
      show("回答の暗号化を解除しました。共有する範囲を選んでください。");
    } finally { unlocking = false; controls(); }
  }
  detail = bindQrShareDialog(root, { client, readSession, canDecrypt: isDecrypted, unlockAnswers,
    catalog: () => catalog, modeLabel, onStopped: refreshShares, onError: handleError });
  node("unlock-here").addEventListener("click", () => run(unlockAnswers));
  node("show-preview").addEventListener("click", () => run(async () => {
    clearPreview();
    await readSession();
    if (!isDecrypted()) throw new Error("先に「回答の暗号化を解除」を押してください。");
    const version = generation;
    const chosen = settings();
    const loaded = await client.loadAnswerDocument();
    answerDocument = loaded.document;
    renderAnswers();
    if (loaded.revision === 0) throw new Error("保存済みの回答がありません。先に回答画面でDBへ保存してください。");
    const payload = plaintextForSelection(loaded.document, catalog, chosen.selection);
    if (generation !== version) throw new Error("設定が変わったため、もう一度内容を確認してください。");
    preview = { ...chosen, payload, key: qrConfirmationKey(chosen.selection, chosen.mode, chosen.expiresAt, session.user_id) };
    const facts = root.createElement("p");
    facts.textContent = `${modeLabel(chosen.mode)} / 期限 ${chosen.expiresAt ? new Date(chosen.expiresAt).toLocaleString() : "なし"}`;
    const answers = root.createElement("div");
    fillAnswerList(answers, entriesFromPayload(payload, catalog));
    node("preview-output").replaceChildren(facts, answers);
    show("内容と期限を確認して、確認欄にチェックしてください。");
  }));
  node("create-qr").addEventListener("click", () => run(async () => {
    await readSession();
    if (!isDecrypted()) throw new Error("先に「回答の暗号化を解除」を押してください。");
    const chosen = settings();
    if (!preview || !node("confirm-share").checked
      || preview.key !== qrConfirmationKey(chosen.selection, chosen.mode, chosen.expiresAt, session.user_id)) {
      clearPreview();
      throw new Error("共有する内容をもう一度確認してください。");
    }
    const created = await client.createQrShare({ ...chosen, payload: structuredClone(preview.payload) });
    node("share-url").value = created.url;
    node("open-link").href = created.url;
    node("link-facts").textContent = `作成日時 ${created.share.created_at} / ${modeLabel(created.share.mode)}`;
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
    renderCatalog();
    if (session.authenticated) {
      encrypted = await client.api("/api/v1/answers/current");
      await refreshShares();
    }
  });
}

if (typeof document !== "undefined") bindSharingPage(document);
