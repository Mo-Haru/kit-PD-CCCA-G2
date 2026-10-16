import {
  api, createFamilyShare, getUnlockState, loadAnswerDocument, login, openOwnerCopy, openReceivedShare, unlock,
  updateFamilyShare,
} from "./client.mjs";
import { entriesFromPayload, fillAnswerList, plaintextForSelection, shareFingerprint } from "./crypto.mjs";

const status = document.querySelector("#status");
const incoming = document.querySelector("#incoming");
const connections = document.querySelector("#connections");
const shares = document.querySelector("#shares");
const received = document.querySelector("#received");
let catalog = { categories: [], questions: [] };
let previewKey = "";

function show(text) {
  status.textContent = text;
}

function button(label, action) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  element.addEventListener("click", action);
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

export function confirmationStillMatches(rememberedKey, selection, payload) {
  return typeof rememberedKey === "string" && rememberedKey !== "" && rememberedKey === shareFingerprint(selection, payload);
}

async function syncAccess() {
  const session = await api("/api/v1/auth/session");
  const unlocked = getUnlockState().unlocked;
  const node = document.querySelector("#lock-state");
  if (!session.authenticated) {
    node.textContent = "ログインしていません。この画面でログインしてから、鍵を解除してください。";
    return session;
  }
  node.textContent = unlocked
    ? `${session.display_name} としてログイン中です。この画面で鍵は解除されています。再認証: ${session.reauthenticated ? "有効" : "必要"}。`
    : `${session.display_name} としてログイン中です。鍵は未解除です。この画面で鍵を解除してください。`;
  return session;
}

function renderCatalog() {
  const chapters = document.querySelector("#chapters");
  const questions = document.querySelector("#questions");
  const chosenChapters = new Set(checked("#chapters"));
  const chosenQuestions = new Set(checked("#questions"));
  chapters.replaceChildren();
  questions.replaceChildren();
  const seen = new Set();
  for (const category of catalog.categories || []) {
    seen.add(category.id);
    chapters.append(choice(category.id, category.name || category.id, chosenChapters.has(category.id)));
  }
  for (const question of catalog.questions || []) {
    if (!seen.has(question.category_id)) {
      seen.add(question.category_id);
      chapters.append(choice(question.category_id, question.category_name || question.category_id, chosenChapters.has(question.category_id)));
    }
    const label = question.text || question.id;
    questions.append(choice(question.id, label, chosenQuestions.has(question.id)));
  }
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
  if (!getUnlockState().unlocked) throw new Error("先にこの画面で鍵を解除してください。");
  const opened = await loadAnswerDocument();
  return plaintextForSelection(opened.document, catalog, currentSelection());
}

function clearConfirmation() {
  previewKey = "";
  const box = document.querySelector("#confirm-share");
  if (box) box.checked = false;
  document.querySelector("#preview-output").replaceChildren();
}

async function requireConfirmedPayload() {
  const payload = await loadSharePayload();
  if (!confirmationStillMatches(previewKey, currentSelection(), payload)) {
    clearConfirmation();
    const error = new Error("確認内容が変わったので、もう一度確認してください。");
    error.code = "confirmation_stale";
    throw error;
  }
  return payload;
}

function toIso(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("期限の形式が正しくありません。");
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function refresh() {
  const session = await syncAccess();
  if (!session.authenticated) return;
  const [invitationBody, connectionBody, ownBody, receivedBody] = await Promise.all([
    api("/api/v1/family/invitations"),
    api("/api/v1/family/connections"),
    api("/api/v1/family/shares"),
    api("/api/v1/family/received"),
  ]);
  incoming.replaceChildren();
  for (const invitation of invitationBody.invitations) {
    const item = document.createElement("li");
    item.textContent = `${invitation.kind} / ${invitation.status} / ${invitation.sender_display_name || ""} → ${invitation.recipient_display_name || "未定"}`;
    if (!invitation.incoming && invitation.status === "awaiting_sender") {
      item.append(button("承認", async () => {
        try {
          await api(`/api/v1/family/invitations/${invitation.id}/confirm`, { method: "POST", body: {} });
          show("接続を承認しました。");
          await refresh();
        } catch (error) {
          show(error.message);
        }
      }));
    }
    if (invitation.status === "pending" || invitation.status === "awaiting_sender") {
      item.append(button("取り消し", async () => {
        try {
          await api(`/api/v1/family/invitations/${invitation.id}/reject`, { method: "POST", body: {} });
          await refresh();
        } catch (error) {
          show(error.message);
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
    item.textContent = `${connection.other_display_name || connection.other_user_id} / ${connection.status}`;
    if (connection.status === "active") {
      const option = document.createElement("option");
      option.value = JSON.stringify({ id: connection.id, userId: connection.other_user_id });
      option.textContent = connection.other_display_name || connection.other_user_id;
      select.append(option);
      item.append(button("切断", async () => {
        await api(`/api/v1/family/connections/${connection.id}/disconnect`, { method: "POST", body: {} });
        show("接続を解除し、共有の配信を停止しました。相手が保存済みの内容は消せません。再接続しても古い共有は戻りません。");
        await refresh();
      }));
    }
    connections.append(item);
  }
  if (previous) select.value = previous;
  shares.replaceChildren();
  for (const share of ownBody.shares) {
    const item = document.createElement("li");
    item.textContent = `${share.recipient_display_name || share.recipient_user_id} / ${share.status} / 版 ${share.current_version} / 期限 ${share.expires_at || "なし"}`;
    if (share.owner_copy) {
      item.append(button("今の内容", async () => {
        try {
          const opened = await openOwnerCopy(share);
          fillAnswerList(document.querySelector("#history-view"), entriesFromPayload(opened, catalog));
        } catch (error) {
          show(error.message);
        }
      }));
    }
    item.append(button("この範囲で更新", async () => {
      if (!document.querySelector("#confirm-share").checked) {
        show("更新する項目を確認してからチェックしてください。");
        return;
      }
      try {
        const payload = await requireConfirmedPayload();
        await updateFamilyShare({
          shareId: share.id,
          expectedVersion: share.current_version,
          recipientUserId: share.recipient_user_id,
          recipientKeyId: share.recipient_key_id,
          selection: currentSelection(),
          payload,
        });
        clearConfirmation();
        show("確認した項目だけを更新しました。回答を保存しただけでは、この版は変わりません。");
        await refresh();
      } catch (error) {
        show(error.message);
      }
    }));
    item.append(button("過去の版", async () => {
      try {
        const history = await api(`/api/v1/family/shares/${share.id}/history`);
        const box = document.querySelector("#history-view");
        box.replaceChildren();
        for (const copy of history.history) {
          box.append(button(`版 ${copy.version} を表示`, async () => {
            const opened = await openOwnerCopy({ id: share.id, owner_copy: copy });
            fillAnswerList(box, entriesFromPayload(opened, catalog));
          }));
        }
      } catch (error) {
        show(error.message);
      }
    }));
    const expiry = document.createElement("input");
    expiry.type = "datetime-local";
    item.append(expiry, button("期限を変更", async () => {
      try {
        await api(`/api/v1/family/shares/${share.id}/expiry`, {
          method: "POST",
          body: { expires_at: toIso(expiry.value) },
        });
        show("共有期限を変更しました。受信者の次の取得から反映されます。");
        await refresh();
      } catch (error) {
        show(error.message);
      }
    }));
    if (share.status !== "stopped") {
      item.append(button("配信停止", async () => {
        await api(`/api/v1/family/shares/${share.id}/stop`, { method: "POST", body: {} });
        show("配信を停止しました。相手が保存済みの内容は消せません。");
        await refresh();
      }));
    }
    shares.append(item);
  }
  received.replaceChildren();
  for (const share of receivedBody.shares) {
    const item = document.createElement("li");
    item.textContent = `${share.owner_display_name || share.owner_user_id} / ${share.status} / 版 ${share.current_version}`;
    item.append(button("開く", async () => {
      try {
        const opened = await openReceivedShare(share);
        fillAnswerList(document.querySelector("#received-view"), entriesFromPayload(opened, catalog));
        show("受け取った回答を表示しました。");
      } catch (error) {
        show(error.message);
      }
    }));
    received.append(item);
  }
}

if (document.querySelector("#share")) {
  document.querySelector("#login-here").addEventListener("click", async () => {
    try {
      await login();
      show("ログインしました。鍵はまだ解除されていません。");
      await refresh();
    } catch (error) {
      show(error.message || "ログインできませんでした。");
    }
  });

  document.querySelector("#unlock-here").addEventListener("click", async () => {
    try {
      await unlock();
      show("この画面で鍵を解除しました。PRFの出力はサーバーへ送っていません。");
      await refresh();
    } catch (error) {
      show(error.message || "鍵を解除できませんでした。鍵は使える状態にしていません。");
    }
  });

  document.querySelector("#invite-open").addEventListener("click", async () => {
    try {
      const created = await api("/api/v1/family/invitations", { method: "POST", body: { kind: "open" } });
      const link = new URL(created.invite_path, location.origin);
      link.hash = `token=${created.token}`;
      document.querySelector("#invite-link").textContent = link.toString();
      show("招待リンクを作りました。トークンはURLの断片にだけ入り、この画面を離れると再表示できません。");
    } catch (error) {
      show(error.message);
    }
  });

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

  document.querySelector("#accept-open").addEventListener("click", async () => {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const token = params.get("token");
    if (!token) {
      show("招待リンクの断片が見つかりません。");
      return;
    }
    try {
      await api("/api/v1/family/invitations/request", { method: "POST", body: { token } });
      history.replaceState(null, "", location.pathname);
      show("参加を申請しました。招待した人が承認すると接続されます。");
      await refresh();
    } catch (error) {
      show(error.message);
    }
  });

  document.querySelector("#show-preview").addEventListener("click", async () => {
    try {
      const payload = await loadSharePayload();
      fillAnswerList(document.querySelector("#preview-output"), entriesFromPayload(payload, catalog));
      previewKey = shareFingerprint(currentSelection(), payload);
      show("確認した内容だけが共有されます。選択や回答が変わった場合は、もう一度確認してください。");
    } catch (error) {
      show(error.message);
    }
  });

  document.querySelector("#share").addEventListener("click", async () => {
    if (!document.querySelector("#confirm-share").checked) {
      show("共有する項目を確認してからチェックしてください。");
      return;
    }
    const selected = document.querySelector("#connection").value;
    if (!selected) {
      show("接続を選んでください。");
      return;
    }
    try {
      const payload = await requireConfirmedPayload();
      const connection = JSON.parse(selected);
      const keys = await api(`/api/v1/encryption-keys/${connection.userId}`);
      const active = keys.keys.find((item) => item.status === "active");
      if (!active) {
        show("相手の受信用公開鍵がありません。");
        return;
      }
      await createFamilyShare({
        connectionId: connection.id,
        recipientUserId: connection.userId,
        recipientKeyId: active.id,
        selection: currentSelection(),
        payload,
        expiresAt: toIso(document.querySelector("#expires-at").value),
      });
      clearConfirmation();
      show("確認した項目だけを共有しました。回答を後から保存しても、この共有は自動では変わりません。");
      await refresh();
    } catch (error) {
      show(error.message);
    }
  });

  api("/api/v1/answers/questions").then((body) => {
    catalog = body;
    renderCatalog();
  }).catch((error) => show(error.message));

  refresh().catch((error) => show(error.message));
}
