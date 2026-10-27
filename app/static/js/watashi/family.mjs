import {
  api, createFamilyShare, getUnlockState, loadAnswerDocument, openOwnerCopy, openReceivedShare, unlock,
  updateFamilyShare,
} from "./client.mjs";
import { entriesFromPayload, fillAnswerList, plaintextForSelection, presentAnswer, shareFingerprint } from "./crypto.mjs";
import qrcode from "../../vendor/qrcode-generator-2.0.4.mjs";

const status = document.querySelector("#status");
const incoming = document.querySelector("#incoming");
const connections = document.querySelector("#connections");
const shares = document.querySelector("#shares");
const received = document.querySelector("#received");
let catalog = { categories: [], questions: [] };
let previewKey = "";
let decryptedAnswers = null;
let answersReady = false;
let encrypted = null;
let authenticated = false;
let unlocking = false;
let knownUser = null;
let needsReauth = false;
let activeDetail = null;
let detailSequence = 0;
let detailTrigger = null;
const stateName = state => ({ active: "共有中", stopped: "停止済み", expired: "期限切れ", key_pending: "再共有が必要" }[state] || state);
const shareState = share => share.status !== "stopped" && share.expires_at && new Date(share.expires_at).getTime() <= Date.now() ? "expired" : share.status;
const node = id => document.querySelector(`#${id}`);

function show(text) {
  status.textContent = text;
  if (node("family-detail")?.open) node("family-detail-status").textContent = text;
}

function scopeVisibility() {
  const scope = document.querySelector("input[name=scope]:checked")?.value || "questions";
  node("chapters").hidden = scope !== "chapters";
  node("questions").hidden = scope !== "questions";
  node("all-answers").hidden = scope !== "all";
}

function syncFamilyControls() {
  const ready = authenticated && answersReady && getUnlockState().unlocked;
  const selection = currentSelection();
  const hasRange = selection.scope === "all" ? catalog.questions.length > 0 : selection.ids.length > 0;
  const expires = node("expires-at");
  const validExpiry = expires.validity?.valid !== false && (!expires.value || new Date(expires.value).getTime() > Date.now());
  const canPreview = ready && hasRange && Boolean(node("connection").value) && validExpiry;
  const control = node("unlock-here");
  control.textContent = ready ? "解除済み" : unlocking ? "解除中…" : "回答の暗号化を解除";
  control.dataset.unlockState = ready ? "unlocked" : "locked";
  control.disabled = !authenticated || unlocking || ready;
  for (const id of ["invite-open", "invite-qr"]) node(id).disabled = !ready || unlocking;
  node("show-preview").disabled = !canPreview || unlocking;
  node("confirm-share").disabled = !ready || !previewKey;
  node("share").disabled = !ready || !previewKey || !node("confirm-share").checked;
}

function button(label, action) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  element.addEventListener("click", async () => {
    try { await action(); }
    catch (error) { showError(error); }
  });
  return element;
}

function shareCard(label, state, action) {
  const element = button("", action);
  element.className = "sharing-issued-card";
  element.dataset.shareState = state;
  const summary = document.createElement("span");
  summary.className = "sharing-issued-summary";
  summary.textContent = label;
  const open = document.createElement("span");
  open.className = "sharing-issued-open";
  open.textContent = "開く";
  element.append(summary, open);
  return element;
}

function checked(selector) {
  return [...document.querySelectorAll(`${selector} input:checked`)].map((item) => item.value);
}

function currentSelection() {
  const scope = document.querySelector("input[name=scope]:checked")?.value;
  if (scope === "all") return { scope: "all" };
  if (scope === "chapters") return { scope: "chapters", ids: checked("#chapters") };
  return { scope: "questions", ids: checked("#questions") };
}

export function confirmationStillMatches(rememberedKey, selection, payload, context) {
  return typeof rememberedKey === "string"
    && rememberedKey !== ""
    && rememberedKey === shareFingerprint(selection, payload, context);
}

export function confirmationCoversShare(context, share) {
  return Boolean(context && context.connectionId && context.recipientKeyId)
    && context.recipientUserId === share.recipient_user_id
    && context.connectionId === share.connection_id;
}

async function syncAccess() {
  const session = await api("/api/v1/auth/session");
  authenticated = session.authenticated;
  if (knownUser && knownUser !== session.user_id) {
    answersReady = false;
    decryptedAnswers = null;
    encrypted = null;
    previewKey = "";
    node("preview-output").replaceChildren();
    node("invite-link").textContent = "";
    node("invite-qr-output").replaceChildren();
    node("invite-output").hidden = true;
    closeFamilyDetail();
    renderCatalog();
    syncFamilyControls();
    throw new Error("アカウントが変わりました。ページを開き直してください。");
  }
  if (session.user_id) knownUser = session.user_id;
  if (!session.authenticated) {
    node("lock-state").textContent = "ヘッダーからログインしてください。ログイン後に回答の暗号化を解除できます。";
    syncFamilyControls();
    return session;
  }
  if (getUnlockState().unlocked && !answersReady && !needsReauth) {
    const loaded = await loadAnswerDocument();
    decryptedAnswers = loaded.document;
    answersReady = true;
    renderCatalog();
  }
  node("lock-state").textContent = answersReady
    ? `${session.display_name} さん：解除済み。家族と共有する回答を確認できます。`
    : `${session.display_name} さん：回答は暗号化されています。内容の確認と共有には解除が必要です。`;
  syncFamilyControls();
  return session;
}

function renderCatalog() {
  const chapters = document.querySelector("#chapters");
  const questions = document.querySelector("#questions");
  const chosenChapters = new Set(checked("#chapters"));
  const chosenQuestions = new Set(checked("#questions"));
  chapters.replaceChildren();
  questions.replaceChildren();
  node("all-answers").replaceChildren();
  const seen = new Set();
  for (const category of catalog.categories || []) {
    seen.add(category.id);
    const group = document.createElement("div");
    group.className = "sharing-chapter";
    group.append(choice(category.id, category.name || category.id, chosenChapters.has(category.id)));
    for (const question of catalog.questions.filter(question => question.category_id === category.id)) appendAnswer(group, question);
    chapters.append(group);
  }
  for (const question of catalog.questions || []) {
    if (!seen.has(question.category_id)) {
      seen.add(question.category_id);
      chapters.append(choice(question.category_id, question.category_name || question.category_id, chosenChapters.has(question.category_id)));
    }
    const label = choice(question.id, "", chosenQuestions.has(question.id));
    appendAnswer(label, question);
    questions.append(label);
    appendAnswer(node("all-answers"), question);
  }
  scopeVisibility();
  syncFamilyControls();
}

function appendAnswer(parent, question) {
  const row = document.createElement("span");
  row.className = "sharing-answer-row";
  const title = document.createElement("span");
  title.className = "sharing-question-title";
  title.textContent = question.text || question.id;
  const value = document.createElement("span");
  value.className = "sharing-answer-value";
  const ready = answersReady && getUnlockState().unlocked;
  value.dataset.answerState = ready ? "decrypted" : "encrypted";
  const answer = presentAnswer(decryptedAnswers?.items?.[question.id]);
  value.textContent = ready ? (answer.stateLabel === "回答" ? `回答：${answer.valueText}` : answer.stateLabel)
    : !authenticated ? "ログインして回答を確認" : encrypted?.document === null ? "保存済みの回答はありません"
      : encrypted?.document ? `暗号化中：${encrypted.document.ciphertext.slice(0, 64)}…` : "回答データを確認しています…";
  row.append(title, value);
  parent.append(row);
}

function choice(value, label, selected) {
  const row = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.value = value;
  input.checked = selected;
  row.append(input, document.createTextNode(` ${label}`));
  return row;
}

async function loadSharePayload() {
  if (!getUnlockState().unlocked || !answersReady) throw new Error("先に「回答の暗号化を解除」を押してください。");
  const opened = await loadAnswerDocument();
  decryptedAnswers = opened.document;
  renderCatalog();
  return plaintextForSelection(opened.document, catalog, currentSelection());
}

function clearConfirmation() {
  previewKey = "";
  const box = document.querySelector("#confirm-share");
  if (box) box.checked = false;
  document.querySelector("#preview-output").replaceChildren();
  syncFamilyControls();
}

async function currentConfirmation() {
  const selected = document.querySelector("#connection").value;
  if (!selected) throw new Error("接続を選んでください。");
  const connection = JSON.parse(selected);
  const keys = await api(`/api/v1/encryption-keys/${connection.userId}`);
  const active = (keys.keys || []).find((item) => item.status === "active");
  if (!active) throw new Error("相手の受信用公開鍵がありません。");
  const select = document.querySelector("#connection");
  const recipientName = select.selectedOptions && select.selectedOptions[0]
    ? select.selectedOptions[0].textContent
    : connection.userId;
  return {
    connectionId: connection.id,
    recipientUserId: connection.userId,
    recipientKeyId: active.id,
    expiresAt: toIso(document.querySelector("#expires-at").value),
    recipientName,
  };
}

function showConfirmedFacts(context) {
  const output = document.querySelector("#preview-output");
  const lines = [
    `相手：${context.recipientName}`,
    `鍵：${context.recipientKeyId}`,
    `期限：${context.expiresAt || "なし"}`,
  ];
  for (const line of lines) {
    const row = document.createElement("p");
    row.textContent = line;
    output.append(row);
  }
}

async function requireConfirmedPayload() {
  const payload = await loadSharePayload();
  const context = await currentConfirmation();
  if (!confirmationStillMatches(previewKey, currentSelection(), payload, context)) {
    clearConfirmation();
    const error = new Error("確認内容が変わったので、もう一度確認してください。相手、期限、鍵、項目を確認し直してください。");
    error.code = "confirmation_stale";
    throw error;
  }
  return { payload, context };
}

function toIso(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("期限の形式が正しくありません。");
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function closeFamilyDetail() {
  detailSequence += 1;
  activeDetail = null;
  node("family-detail-body").replaceChildren();
  if (node("family-detail").open) node("family-detail").close();
  if (detailTrigger?.isConnected === false) node("unlock-here").focus();
  else detailTrigger?.focus();
  detailTrigger = null;
}

function syncDetailStops() {
  for (const id of ["family-stop-top", "family-stop-bottom"]) {
    node(id).hidden = activeDetail?.kind !== "own";
    node(id).disabled = !activeDetail?.share || activeDetail.share.status === "stopped";
    node(id).textContent = activeDetail?.share?.status === "stopped" ? "配信停止済み" : "配信停止";
  }
}

async function openFamilyDetail(id, kind, opener) {
  const ticket = ++detailSequence;
  if (opener) detailTrigger = opener;
  activeDetail = { id, kind, share: null };
  node("family-detail-body").replaceChildren();
  node("family-detail-status").textContent = "共有内容を読み込んでいます…";
  node("family-detail-unlock").hidden = true;
  node("family-detail-title").textContent = kind === "own" ? "家族に共有した内容" : "家族から共有された内容";
  if (!node("family-detail").open) node("family-detail").showModal();
  syncDetailStops();
  try {
    await syncAccess();
    const result = await api(kind === "own" ? "/api/v1/family/shares" : `/api/v1/family/received/${id}`);
    if (ticket !== detailSequence || !node("family-detail").open) return;
    const share = kind === "own" ? result.shares.find(share => share.id === id) : result.share;
    if (!share) throw new Error("この共有は見つかりません。停止・期限切れなどの可能性があります。");
    activeDetail.share = share;
    syncDetailStops();
    const own = kind === "own" ? buildOwnDetail(share) : { body: document.createElement("div"), answers: document.createElement("div") };
    if (kind !== "own") {
      const facts = document.createElement("p");
      facts.textContent = `${share.owner_display_name || "家族"} さんから · ${stateName(share.status)} · 版 ${share.current_version}`;
      own.body.append(facts, own.answers);
    }
    node("family-detail-body").append(own.body);
    const ready = answersReady && getUnlockState().unlocked;
    node("family-detail-unlock").hidden = ready;
    if (!ready) {
      node("family-detail-status").textContent = "回答は暗号化されています。解除すると共有内容を表示できます。";
      return;
    }
    if (kind !== "own" && !share.current) throw new Error("家族の鍵が変わったため、再共有されるまで内容を表示できません。");
    if (kind === "own" && !share.owner_copy) {
      node("family-detail-status").textContent = "発行時点の回答の控えがありません。共有設定と更新操作を表示しています。";
      return;
    }
    const payload = kind === "own" ? await openOwnerCopy(share) : await openReceivedShare(share);
    if (ticket !== detailSequence || !node("family-detail").open) return;
    fillAnswerList(own.answers, entriesFromPayload(payload, catalog));
    node("family-detail-status").textContent = kind === "own" ? "発行時点の回答を表示しています。" : "家族から共有された回答を復号して表示しています。";
  } catch (error) {
    if (ticket === detailSequence) node("family-detail-status").textContent = error.message || "共有内容を表示できませんでした。";
  }
}

async function stopFamilyDetail() {
  if (!activeDetail?.share || activeDetail.kind !== "own" || activeDetail.share.status === "stopped") return;
  if (!globalThis.confirm("この共有を停止しますか？\n停止すると、この家族への回答の配信が止まります。相手が保存済みの回答は削除されません。")) return;
  const current = activeDetail;
  for (const id of ["family-stop-top", "family-stop-bottom"]) node(id).disabled = true;
  try {
    await api(`/api/v1/family/shares/${current.id}/stop`, { method: "POST", body: {} });
    current.share.status = "stopped";
    if (activeDetail === current) node("family-detail-status").textContent = "配信を停止しました。相手の次の取得から反映されます。";
    await refresh();
    if (node("family-detail").open && activeDetail?.id === current.id) {
      await openFamilyDetail(current.id, "own");
      node("family-detail-status").textContent = "配信を停止しました。相手の次の取得から反映されます。";
    }
  } catch (error) {
    showError(error);
    if (activeDetail === current) node("family-detail-status").textContent = error.message;
  } finally { syncDetailStops(); }
}

function showError(error) {
  if (error.code === "reauth_required" || error.status === 401) {
    needsReauth = true;
    answersReady = false;
    decryptedAnswers = null;
    previewKey = "";
    node("preview-output").replaceChildren();
    renderCatalog();
    if (node("family-detail").open) {
      node("family-detail-body").replaceChildren();
      node("family-detail-unlock").hidden = false;
    }
  }
  show(error.code === "reauth_required" ? "本人確認の期限が切れました。回答の暗号化をもう一度解除してください。" : error.message || "操作できませんでした。");
}

async function unlockFamily() {
  if (unlocking) return;
  unlocking = true;
  syncFamilyControls();
  try {
    await unlock();
    const loaded = await loadAnswerDocument();
    decryptedAnswers = loaded.document;
    answersReady = true;
    needsReauth = false;
    await refresh();
    show("回答の暗号化を解除しました。家族と共有する内容を確認できます。");
  } finally { unlocking = false; syncFamilyControls(); }
}

async function createInvitation(withQr) {
  if (!answersReady || !getUnlockState().unlocked) throw new Error("先に回答の暗号化を解除してください。");
  const created = await api("/api/v1/family/invitations", { method: "POST", body: { kind: "open" } });
  const link = new URL(created.invite_path, location.origin);
  link.hash = `token=${created.token}`;
  node("invite-link").textContent = link.toString();
  node("invite-output").hidden = false;
  node("invite-qr-output").replaceChildren();
  if (withQr) {
    const code = qrcode(0, "M");
    code.addData(link.toString());
    code.make();
    const image = document.createElement("img");
    image.src = code.createDataURL(6, 4);
    image.alt = "家族への招待QRコード";
    node("invite-qr-output").append(image);
  }
  show(withQr ? "招待QRコードを作成しました。家族に読み取ってもらってください。" : "招待リンクを作成しました。家族に送ってください。");
  await refresh();
}


function buildOwnDetail(share) {
    const item = document.createElement("div");
    item.textContent = `${share.recipient_display_name || "家族"} さんへ · ${stateName(share.status)} · 版 ${share.current_version} · 期限 ${share.expires_at || "なし"}`;
    const view = document.createElement("div");
    item.append(view);
    if (share.status === "key_pending") {
      item.append(document.createTextNode(" 相手の受信用鍵が変わっています。確認した現在の鍵で更新してください。"));
    }
    if (share.owner_copy) {
      item.append(button("今の内容", async () => {
        try {
          const opened = await openOwnerCopy(share);
          fillAnswerList(view, entriesFromPayload(opened, catalog));
        } catch (error) {
          showError(error);
        }
      }));
    }
    item.append(button("この範囲で更新", async () => {
      if (!document.querySelector("#confirm-share").checked) {
        show("更新する項目を確認してからチェックしてください。");
        return;
      }
      try {
        const confirmed = await requireConfirmedPayload();
        if (!confirmationCoversShare(confirmed.context, share)) {
          clearConfirmation();
          show("確認した相手と接続がこの共有と違います。もう一度確認してください。");
          return;
        }
        await updateFamilyShare({
          shareId: share.id,
          expectedVersion: share.current_version,
          recipientUserId: share.recipient_user_id,
          recipientKeyId: confirmed.context.recipientKeyId,
          selection: currentSelection(),
          payload: confirmed.payload,
        });
        clearConfirmation();
        show("確認した相手と鍵の共有を、確認した項目だけに更新しました。回答を保存しただけでは、この版は変わりません。");
        await refresh();
        if (node("family-detail").open && activeDetail?.id === share.id) await openFamilyDetail(share.id, "own");
      } catch (error) {
        showError(error);
      }
    }));
    item.append(button("過去の版", async () => {
      try {
        const history = await api(`/api/v1/family/shares/${share.id}/history`);
        const box = view;
        box.replaceChildren();
        for (const copy of history.history) {
          box.append(button(`版 ${copy.version} を表示`, async () => {
            const opened = await openOwnerCopy({ id: share.id, owner_copy: copy });
            fillAnswerList(box, entriesFromPayload(opened, catalog));
          }));
        }
      } catch (error) {
        showError(error);
      }
    }));
    const expiryPanel = document.createElement("div");
    expiryPanel.hidden = true;
    item.append(button("期限を変更", () => { expiryPanel.hidden = !expiryPanel.hidden; }));
    const expiry = document.createElement("input");
    expiry.type = "datetime-local";
    if (share.expires_at) {
      const date = new Date(share.expires_at);
      expiry.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }
    expiry.id = `share-expiry-${share.id}`;
    expiry.className = "md-field-control";
    const expiryField = document.createElement("div");
    expiryField.className = "md-field md-field--raised";
    const expiryLabel = document.createElement("label");
    expiryLabel.className = "md-field-label";
    expiryLabel.htmlFor = expiry.id;
    expiryLabel.textContent = "変更後の期限";
    expiryField.append(expiryLabel, expiry);
    expiryPanel.append(expiryField, button("変更を保存", async () => {
      try {
        await api(`/api/v1/family/shares/${share.id}/expiry`, {
          method: "POST",
          body: { expires_at: toIso(expiry.value) },
        });
        expiryPanel.hidden = true;
        node("family-detail-status").textContent = "共有期限を変更しました。受信者の次の取得から反映されます。";
        await refresh();
        if (node("family-detail").open && activeDetail?.id === share.id) {
          await openFamilyDetail(share.id, "own");
          node("family-detail-status").textContent = "共有期限を変更しました。受信者の次の取得から反映されます。";
        }
      } catch (error) {
        showError(error);
      }
    }));
    item.append(expiryPanel);
    return { body: item, answers: view };
}

async function refresh() {
  const session = await syncAccess();
  if (!session.authenticated) return;
  encrypted = await api("/api/v1/answers/current");
  renderCatalog();
  const [invitationBody, connectionBody, ownBody, receivedBody] = await Promise.all([
    api("/api/v1/family/invitations"),
    api("/api/v1/family/connections"),
    api("/api/v1/family/shares"),
    api("/api/v1/family/received"),
  ]);
  incoming.replaceChildren();
  for (const invitation of invitationBody.invitations) {
    const item = document.createElement("li");
    item.className = "record-card";
    const states = { pending: "承諾を待っています", awaiting_sender: "家族登録の確認待ち", accepted: "家族に登録済み", expired: "期限切れ", rejected: "取り消し済み" };
    item.textContent = `${invitation.incoming ? invitation.sender_display_name || "家族" : invitation.recipient_display_name || "招待した方"} · ${states[invitation.status] || "招待終了"}`;
    if (!invitation.incoming && invitation.status === "awaiting_sender") {
      item.append(button("家族に追加", async () => {
        try {
          await api(`/api/v1/family/invitations/${invitation.id}/confirm`, { method: "POST", body: {} });
          show("家族に追加しました。");
          await refresh();
        } catch (error) {
          showError(error);
        }
      }));
    }
    if (invitation.status === "pending" || invitation.status === "awaiting_sender") {
      item.append(button("取り消し", async () => {
        try {
          await api(`/api/v1/family/invitations/${invitation.id}/reject`, { method: "POST", body: {} });
          await refresh();
        } catch (error) {
          showError(error);
        }
      }));
    }
    incoming.append(item);
  }
  connections.replaceChildren();
  const select = document.querySelector("#connection");
  const previous = select.value;
  select.replaceChildren();
  for (const connection of connectionBody.connections) {
    const item = document.createElement("li");
    item.className = "record-card";
    const name = document.createElement("span");
    name.textContent = connection.other_display_name || "家族";
    const state = document.createElement("span");
    state.className = "family-availability";
    state.textContent = connection.status === "active" ? "✓" : "−";
    state.setAttribute("role", "img");
    state.setAttribute("aria-label", connection.status === "active" ? "共有できる家族" : "登録解除済み");
    item.append(state, name);
    if (connection.status === "active") {
      const option = document.createElement("option");
      option.value = JSON.stringify({ id: connection.id, userId: connection.other_user_id });
      option.textContent = connection.other_display_name || connection.other_user_id;
      select.append(option);
      item.append(button("家族から外す", async () => {
        await api(`/api/v1/family/connections/${connection.id}/disconnect`, { method: "POST", body: {} });
        show("家族の登録を解除し、共有を停止しました。相手が保存済みの内容は消せません。");
        await refresh();
      }));
    }
    connections.append(item);
  }
  if (previous) select.value = previous;
  shares.replaceChildren();
  for (const share of ownBody.shares) {
    const card = document.createElement("li");
    card.className = "record-card";
    const state = shareState(share);
    const open = shareCard(`${share.recipient_display_name || "家族"} に共有 · ${stateName(state)}`, state, () => openFamilyDetail(share.id, "own", open));
    card.append(open);
    shares.append(card);
  }
  received.replaceChildren();
  for (const share of receivedBody.shares) {
    const card = document.createElement("li");
    card.className = "record-card";
    const state = shareState(share);
    const open = shareCard(`${share.owner_display_name || "家族"} さんからの共有 · ${stateName(state)}`, state, () => openFamilyDetail(share.id, "received", open));
    card.append(open);
    received.append(card);
  }
  if (!ownBody.shares.length) shares.textContent = "家族に共有した回答はまだありません。";
  if (!receivedBody.shares.length) received.textContent = "家族から共有された回答はまだありません。";
  syncFamilyControls();
}

if (document.querySelector("#share")) {
  node("unlock-here").addEventListener("click", () => unlockFamily().catch(showError));
  node("invite-open").addEventListener("click", () => createInvitation(false).catch(showError));
  node("invite-qr").addEventListener("click", () => createInvitation(true).catch(showError));
  for (const id of ["family-close-top", "family-close-bottom"]) node(id).addEventListener("click", closeFamilyDetail);
  node("family-detail").addEventListener("cancel", event => { event.preventDefault(); closeFamilyDetail(); });
  node("family-detail").addEventListener("close", () => {
    detailSequence += 1;
    node("family-detail-body").replaceChildren();
  });
  for (const id of ["family-stop-top", "family-stop-bottom"]) node(id).addEventListener("click", stopFamilyDetail);
  node("family-detail-unlock").addEventListener("click", async () => {
    try {
      await unlockFamily();
      if (activeDetail) await openFamilyDetail(activeDetail.id, activeDetail.kind);
    } catch (error) { showError(error); node("family-detail-status").textContent = error.message; }
  });
  for (const input of document.querySelectorAll("input[name=scope]")) input.addEventListener("change", () => {
    scopeVisibility();
    clearConfirmation();
  });
  for (const id of ["questions", "chapters", "connection", "expires-at"]) node(id).addEventListener("change", clearConfirmation);
  node("expires-at").addEventListener("input", clearConfirmation);
  node("confirm-share").addEventListener("change", syncFamilyControls);
  document.querySelector("#copy-invite").addEventListener("click", async () => {
    const link = document.querySelector("#invite-link").textContent;
    if (!link) {
      show("先に招待リンクを作ってください。");
      return;
    }
    try {
      await navigator.clipboard.writeText(link);
      show("招待リンクをコピーしました。");
    } catch (_error) {
      show("コピーできなかったので、表示中のリンクを選んで共有してください。");
    }
  });

  document.querySelector("#show-preview").addEventListener("click", async () => {
    try {
      const payload = await loadSharePayload();
      const context = await currentConfirmation();
      fillAnswerList(document.querySelector("#preview-output"), entriesFromPayload(payload, catalog));
      showConfirmedFacts(context);
      previewKey = shareFingerprint(currentSelection(), payload, context);
      syncFamilyControls();
      show("確認した相手、期限、鍵、項目だけが共有されます。どれかを変えた場合は、もう一度確認してください。");
    } catch (error) {
      showError(error);
    }
  });

  document.querySelector("#share").addEventListener("click", async () => {
    if (!document.querySelector("#confirm-share").checked) {
      show("共有する項目を確認してからチェックしてください。");
      return;
    }
    try {
      const confirmed = await requireConfirmedPayload();
      await createFamilyShare({
        connectionId: confirmed.context.connectionId,
        recipientUserId: confirmed.context.recipientUserId,
        recipientKeyId: confirmed.context.recipientKeyId,
        selection: currentSelection(),
        payload: confirmed.payload,
        expiresAt: confirmed.context.expiresAt,
      });
      clearConfirmation();
      show("確認した相手、期限、鍵、項目だけを共有しました。回答を後から保存しても、この共有は自動では変わりません。");
      await refresh();
    } catch (error) {
      showError(error);
    }
  });

  api("/api/v1/answers/questions").then((body) => {
    catalog = body;
    renderCatalog();
  }).catch((error) => show(error.message));

  refresh().catch((error) => show(error.message));
}
