import { api, createFamilyShare, getUnlockState, openOwnerCopy, openReceivedShare } from "./client.mjs";

const status = document.querySelector("#status");
const incoming = document.querySelector("#incoming");
const connections = document.querySelector("#connections");
const shares = document.querySelector("#shares");
const received = document.querySelector("#received");

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

async function refresh() {
  const session = await api("/api/v1/auth/session");
  if (!session.authenticated) {
    show("先にパスキーでログインしてください。");
    return;
  }
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
        show("接続を解除し、共有の配信を停止しました。相手が保存済みの内容は消せません。");
        await refresh();
      }));
    }
    connections.append(item);
  }
  shares.replaceChildren();
  for (const share of ownBody.shares) {
    const item = document.createElement("li");
    item.textContent = `${share.recipient_display_name || ""} / ${share.status} / 版 ${share.current_version}`;
    if (getUnlockState().unlocked && share.owner_copy) {
      item.append(button("自分の履歴を表示", async () => {
        const opened = await openOwnerCopy(share);
        show(`共有した項目: ${Object.keys(opened.items || {}).join(", ") || "なし"}`);
      }));
    }
    if (share.status !== "stopped") {
      item.append(button("配信停止", async () => {
        await api(`/api/v1/family/shares/${share.id}/stop`, { method: "POST", body: {} });
        await refresh();
      }));
    }
    shares.append(item);
  }
  received.replaceChildren();
  for (const share of receivedBody.shares) {
    const item = document.createElement("li");
    item.textContent = `${share.owner_display_name || ""} / ${share.status} / 版 ${share.current_version}`;
    item.append(button("開く", async () => {
      try {
        const opened = await openReceivedShare(share);
        show(`受け取った項目: ${Object.keys(opened.items || {}).join(", ") || "なし"}`);
      } catch (error) {
        show(error.message);
      }
    }));
    received.append(item);
  }
}

document.querySelector("#invite-open").addEventListener("click", async () => {
  try {
    const created = await api("/api/v1/family/invitations", { method: "POST", body: { kind: "open" } });
    const link = new URL(created.invite_path, location.origin);
    link.hash = `token=${created.token}`;
    document.querySelector("#invite-link").textContent = link.toString();
    show("招待リンクを作りました。この画面を離れるとトークンは再表示できません。リンク先の人を承認するまで接続されません。");
  } catch (error) {
    show(error.message);
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
  const connection = JSON.parse(selected);
  const ids = document.querySelector("#item-ids").value.split(",").map((item) => item.trim()).filter(Boolean);
  try {
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
      selectedIds: ids,
    });
    show("選択した項目だけを共有しました。回答を後から保存しても、この共有は自動では変わりません。");
    await refresh();
  } catch (error) {
    show(error.message);
  }
});

refresh().catch((error) => show(error.message));
