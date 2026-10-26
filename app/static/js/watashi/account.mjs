import { addPasskey, api, getUnlockState, logout, unlock } from "./client.mjs";

const status = document.querySelector("#status");
const list = document.querySelector("#passkeys");
const loginUrl = document.querySelector("#account-settings").dataset.loginUrl;
let busy = false;
let sessionKnown = false;
let authenticated = false;

function show(text, isError = false) {
  status.textContent = text;
  status.classList.toggle("is-error", isError);
}

function syncControls() {
  for (const button of document.querySelectorAll("#account-settings button")) {
    button.disabled = busy || !sessionKnown || !authenticated;
  }
}

async function runAction(button, pending, action, success) {
  if (busy || !sessionKnown) return;
  busy = true;
  const label = button.textContent;
  button.textContent = pending;
  syncControls();
  show(pending);
  let completed = false;
  try {
    await action();
    completed = true;
    await refresh();
    show(success);
  } catch (error) {
    const message = error.name === "NotAllowedError" || error.name === "AbortError"
      ? "パスキーの操作が完了しませんでした。端末の案内を確認し、もう一度お試しください。"
      : error.message || "操作できませんでした。もう一度お試しください。";
    show(completed ? `${success} 表示を更新できませんでした。ページを再読み込みしてください。` : message, true);
  } finally {
    busy = false;
    button.textContent = label;
    syncControls();
  }
}

async function refresh() {
  sessionKnown = false;
  syncControls();
  const session = await api("/api/v1/auth/session");
  authenticated = session.authenticated;
  sessionKnown = true;
  document.querySelector("#account-actions").hidden = !authenticated;
  const unlocked = getUnlockState().unlocked;
  const sessionLabel = document.querySelector("#session");
  sessionLabel.textContent = authenticated
    ? `${session.display_name} としてログイン中です。鍵の解除: ${unlocked ? "済み" : "未"}。再認証: ${session.reauthenticated ? "有効" : "必要"}。`
    : "ログインが必要です。ログイン画面へ移動します。";
  syncControls();
  if (!authenticated) {
    list.replaceChildren();
    location.replace(loginUrl);
    return;
  }
  const passkeys = await api("/api/v1/auth/passkeys");
  list.replaceChildren();
  for (const passkey of passkeys.passkeys) {
    const item = document.createElement("li");
    const label = passkey.label || "名称なし";
    const state = passkey.revoked_at ? "無効" : passkey.proven_at ? "有効" : "確認待ち";
    item.textContent = `${label} / ${state} / 作成 ${passkey.created_at || ""}`;
    if (!passkey.revoked_at) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "このパスキーを無効化";
      button.addEventListener("click", async () => {
        const active = passkeys.passkeys.filter((row) => !row.revoked_at && row.proven_at);
        const body = {};
        if (passkey.proven_at && active.length <= 1) {
          if (!document.querySelector("#confirm-last").checked) {
            show("最後のパスキーを無効化するには、データを復号できなくなる確認にチェックしてください。");
            return;
          }
          body.confirm_last = true;
        }
        try {
          await api(`/api/v1/auth/passkeys/${passkey.id}/disable`, { method: "POST", body });
          show("パスキーを無効化しました。");
          await refresh();
        } catch (error) {
          show(error.message);
        }
      });
      item.append(button);
    }
    list.append(item);
  }
  syncControls();
}


document.querySelector("#unlock").addEventListener("click", (event) => {
  runAction(event.currentTarget, "鍵を解除しています…", unlock, "鍵を解除しました。PRFの出力はサーバーへ送っていません。");
});

document.querySelector("#logout").addEventListener("click", (event) => {
  runAction(event.currentTarget, "ログアウトしています…", logout, "ログアウトしました。");
});

document.querySelector("#add-passkey").addEventListener("click", (event) => {
  runAction(event.currentTarget, "パスキーを追加しています…", addPasskey, "パスキーを追加しました。以前のパスキーも有効なままです。");
});

refresh().catch(() => show("ログイン状態を確認できませんでした。接続を確認して、ページを再読み込みしてください。", true));
