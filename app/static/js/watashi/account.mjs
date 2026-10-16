import { addPasskey, api, detectPrf, getUnlockState, login, logout, registerAccount, unlock } from "./client.mjs";

const status = document.querySelector("#status");
const list = document.querySelector("#passkeys");

function show(text) {
  status.textContent = text;
}

async function refresh() {
  const session = await api("/api/v1/auth/session");
  const unlocked = getUnlockState().unlocked;
  document.querySelector("#session").textContent = session.authenticated
    ? `${session.display_name} としてログイン中です。鍵の解除: ${unlocked ? "済み" : "未"}。再認証: ${session.reauthenticated ? "有効" : "必要"}。`
    : "ログインしていません。";
  if (!session.authenticated) {
    list.replaceChildren();
    return;
  }
  const passkeys = await api("/api/v1/auth/passkeys");
  list.replaceChildren();
  for (const passkey of passkeys.passkeys) {
    const item = document.createElement("li");
    const label = passkey.label || "名称なし";
    const state = passkey.revoked_at ? "無効" : "有効";
    item.textContent = `${label} / ${state} / 作成 ${passkey.created_at || ""}`;
    if (!passkey.revoked_at) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "このパスキーを無効化";
      button.addEventListener("click", async () => {
        const active = passkeys.passkeys.filter((row) => !row.revoked_at);
        const body = {};
        if (active.length <= 1) {
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
}

document.querySelector("#register").addEventListener("click", async () => {
  const prf = await detectPrf();
  if (!prf.supported) {
    show(prf.reason);
    return;
  }
  try {
    await registerAccount(document.querySelector("#display-name").value);
    show("パスキーを登録し、この端末で鍵を解除しました。");
    await refresh();
  } catch (error) {
    show(error.message || "登録できませんでした。");
  }
});

document.querySelector("#login").addEventListener("click", async () => {
  try {
    await login();
    show("ログインしました。回答を読むには鍵の解除が必要です。");
    await refresh();
  } catch (error) {
    show(error.message || "ログインできませんでした。");
  }
});

document.querySelector("#unlock").addEventListener("click", async () => {
  try {
    await unlock();
    show("鍵を解除しました。PRFの出力はサーバーへ送っていません。");
    await refresh();
  } catch (error) {
    show(error.message || "解除できませんでした。");
  }
});

document.querySelector("#logout").addEventListener("click", async () => {
  try {
    await logout();
    show("ログアウトしました。");
    await refresh();
  } catch (error) {
    show(error.message);
  }
});

document.querySelector("#add-passkey").addEventListener("click", async () => {
  try {
    await addPasskey();
    show("パスキーを追加しました。以前のパスキーも有効なままです。");
    await refresh();
  } catch (error) {
    show(error.message || "パスキーを追加できませんでした。");
  }
});

refresh().catch((error) => show(error.message));
