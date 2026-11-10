import { notify } from "./site-alert.mjs";
import { b64urlToBytes, bytesToB64url } from "./bytes.mjs";
import {
  api, confirmMigrationAccess, getUnlockState, login, registerTransferredPasskey, unlock, unlockedKeyMaterial,
} from "./client.mjs";
import { generateHpkeKeyPair, hpkeAad, hpkeOpen, hpkeSeal } from "./crypto.mjs";

const status = document.querySelector("#status");
const SOURCE_KEY = "wn-transfer-source";
let sourceId = sessionStorage.getItem(SOURCE_KEY) || "";
let targetPrivate = null;
let timer = 0;
let decryptConfirmed = false;

function show(text, error = false) {
  notify(document, status, text, error);
}

async function fingerprintOf(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("").slice(0, 16);
  return hex.match(/.{4}/g).join(" ");
}

async function syncAccess() {
  const session = await api("/api/v1/auth/session");
  const unlocked = getUnlockState().unlocked;
  const node = document.querySelector("#lock-state");
  if (!session.authenticated) {
    node.textContent = "ログインしていません。この画面でログインしてから、鍵を解除してください。";
    return;
  }
  node.textContent = unlocked
    ? `${session.display_name} としてログイン中です。この画面で鍵は解除されています。`
    : `${session.display_name} としてログイン中です。鍵は未解除です。この画面で鍵を解除してください。`;
}

const SOURCE_ENDED = {
  finalized: "移行が完了しました。この端末のパスキーは無効になりました。",
  cancelled: "移行は取り消されました。この端末のパスキーはそのまま使えます。",
  expired: "移行の期限が切れました。この端末のパスキーはそのまま使えます。必要なら、もう一度開始してください。",
};

function stopSource(text) {
  window.clearInterval(timer);
  timer = 0;
  sessionStorage.removeItem(SOURCE_KEY);
  show(text);
}

// 新しい端末が完了・取消するまで、状態を見続ける。
function watchSource() {
  window.clearInterval(timer);
  timer = window.setInterval(() => {
    refreshSource().catch((error) => show(error.message, true));
  }, 2000);
}

// ログインし直したあと、進行中の移行があれば、状態を見るのを再開する。
async function resumeSource() {
  if (!sourceId || !sessionStorage.getItem(SOURCE_KEY)) return;
  if (!timer) watchSource();
  await refreshSource();
}

async function refreshSource() {
  if (!sourceId) return;
  let migration;
  try {
    migration = (await api(`/api/v1/transfer/${sourceId}`)).migration;
  } catch (error) {
    // ログインが切れると状態を読めない。新しい端末が完了した場合も、通常のログアウトや期限切れの場合も同じ401になる。
    // どちらか区別できないので、移行IDは残して見るのを止め、ログインし直したら再開する（完了・取消・期限切れを確認できたときだけIDを消す）。
    if (error.status === 401 && timer) {
      window.clearInterval(timer);
      timer = 0;
      document.querySelector("#source-state").textContent = "ログインが切れたため、状態を確認できません";
      show("この端末のログインが終了しました。新しい端末で移行を完了した場合、この端末のパスキーは無効になっています。まだ完了していない場合は、この画面でログインし直して鍵を解除すると、続きを確認できます。");
      await syncAccess().catch(() => {});
      return;
    }
    throw error;
  }
  document.querySelector("#source-id").textContent = sourceId;
  document.querySelector("#source-state").textContent = `${migration.status} / 期限 ${migration.expires_at}`;
  document.querySelector("#source-code").textContent = migration.confirmation_code || "まだありません";
  document.querySelector("#source-fingerprint").textContent = migration.fingerprint || "まだありません";
  if (migration.status === "source_approved") await uploadBundle(migration);
  else if (SOURCE_ENDED[migration.status]) stopSource(SOURCE_ENDED[migration.status]);
}

async function uploadBundle(migration) {
  const material = unlockedKeyMaterial();
  if (!material || !migration.target_public_key) {
    show("鍵束を送るには、この画面で鍵を解除してください。別の画面で解除しても、戻ると鍵は残っていません。");
    return;
  }
  const aad = hpkeAad({
    purpose: "migration",
    contextId: migration.id,
    ownerId: material.userId,
    recipientId: "target",
    keyId: migration.target_public_key,
    version: 1,
  });
  const sealed = await hpkeSeal(
    b64urlToBytes(migration.target_public_key),
    new TextEncoder().encode(JSON.stringify({
      v: 1,
      storageKey: bytesToB64url(material.storageKey),
      dataKeyId: material.dataKeyId,
    })),
    aad,
  );
  await api(`/api/v1/transfer/${migration.id}/bundle`, {
    method: "POST",
    body: { hpke_enc: bytesToB64url(sealed.enc), ciphertext: bytesToB64url(sealed.ciphertext) },
  });
  show("鍵束を送りました。古いパスキーは、新しい端末が復号確認と最終確定を終えるまで有効です。");
}

document.querySelector("#login-here").addEventListener("click", async () => {
  try {
    await login();
    decryptConfirmed = false;
    await syncAccess();
    // 移行の状態を読むには、ログインに加えて鍵の解除（本人確認）が要る。
    show(sourceId && sessionStorage.getItem(SOURCE_KEY)
      ? "ログインしました。「この画面で鍵を解除」を押すと、進行中の移行の続きを確認できます。"
      : "ログインしました。鍵はまだ解除されていません。");
  } catch (error) {
    show(error.message || "ログインできませんでした。", true);
  }
});

document.querySelector("#unlock-here").addEventListener("click", async () => {
  try {
    await unlock();
    show("この画面で鍵を解除しました。");
    await syncAccess();
    await resumeSource();
  } catch (error) {
    show(error.message || "鍵を解除できませんでした。鍵は使える状態にしていません。", true);
  }
});

document.querySelector("#source-start").addEventListener("click", async () => {
  try {
    const started = await api("/api/v1/transfer/start", { method: "POST", body: {} });
    sourceId = started.migration.id;
    sessionStorage.setItem(SOURCE_KEY, sourceId);
    document.querySelector("#source-id").textContent = sourceId;
    show("移行を開始しました。新しい端末にこのIDを入力してください。");
    watchSource();
    await refreshSource();
  } catch (error) {
    show(error.message, true);
  }
});

document.querySelector("#source-approve").addEventListener("click", async () => {
  try {
    await api(`/api/v1/transfer/${sourceId}/approve`, {
      method: "POST",
      body: {
        confirmation_code: document.querySelector("#typed-code").value,
        fingerprint: document.querySelector("#source-fingerprint").textContent,
      },
    });
    show("コードと指紋を確認しました。");
    await refreshSource();
  } catch (error) {
    show(error.message, true);
  }
});

document.querySelector("#source-cancel").addEventListener("click", async () => {
  try {
    await api(`/api/v1/transfer/${sourceId}/cancel`, { method: "POST", body: {} });
    stopSource("移行を取り消しました。古いパスキーはそのまま使えます。");
  } catch (error) {
    show(error.message, true);
  }
});

document.querySelector("#target-bind").addEventListener("click", async () => {
  try {
    const pair = await generateHpkeKeyPair();
    targetPrivate = pair.privateKey;
    const bound = await api("/api/v1/transfer/bind", {
      method: "POST",
      body: {
        migration_id: document.querySelector("#target-id").value.trim(),
        target_public_key: bytesToB64url(pair.publicKey),
      },
    });
    sessionStorage.setItem("wn-transfer-token", bound.transfer_token);
    sessionStorage.setItem("wn-transfer-id", bound.migration.id);
    const local = await fingerprintOf(pair.publicKey);
    document.querySelector("#target-code").textContent = bound.migration.confirmation_code;
    document.querySelector("#target-fingerprint").textContent = bound.migration.fingerprint;
    if (local !== bound.migration.fingerprint) {
      show("表示された指紋がこの端末の鍵と一致しません。承認しないでください。", true);
      return;
    }
    show("コードと指紋がこの端末の鍵と一致しています。古い端末の表示と比べてください。");
  } catch (error) {
    show(error.message, true);
  }
});

document.querySelector("#target-finish").addEventListener("click", async () => {
  const transferToken = sessionStorage.getItem("wn-transfer-token");
  const migrationId = sessionStorage.getItem("wn-transfer-id");
  if (!transferToken || !targetPrivate || !migrationId) {
    show("先にこの端末の鍵を登録してください。", true);
    return;
  }
  const headers = { "X-Transfer-Token": transferToken };
  try {
    const packed = await api("/api/v1/transfer/target/bundle", { headers });
    const migration = (await api("/api/v1/transfer/target/status", { headers })).migration;
    const aad = hpkeAad({
      purpose: "migration",
      contextId: migrationId,
      ownerId: packed.user_id,
      recipientId: "target",
      keyId: migration.target_public_key,
      version: 1,
    });
    const opened = JSON.parse(new TextDecoder().decode(await hpkeOpen(
      targetPrivate,
      b64urlToBytes(packed.hpke_enc),
      b64urlToBytes(packed.ciphertext),
      aad,
    )));
    await registerTransferredPasskey(transferToken, b64urlToBytes(opened.storageKey));
    decryptConfirmed = false;
    show("新しいパスキーを登録しました。この画面でそのパスキーによりログインし、復号確認をしてから完了してください。古いパスキーはまだ有効です。");
  } catch (error) {
    decryptConfirmed = false;
    show(error.message || "新しい端末での確認がまだできません。古いパスキーは無効化していません。", true);
  }
});

document.querySelector("#target-confirm").addEventListener("click", async () => {
  const migrationId = sessionStorage.getItem("wn-transfer-id") || document.querySelector("#target-id").value.trim();
  decryptConfirmed = false;
  try {
    const result = await confirmMigrationAccess(migrationId);
    decryptConfirmed = true;
    document.querySelector("#confirm-result").textContent = `保管用鍵で既存データを開きました。回答 ${result.answerCount} 件、受信共有 ${result.shareCount} 件。内容はサーバーへ送っていません。`;
    show("復号できました。問題なければ、古いパスキーの無効化を押してください。");
  } catch (error) {
    decryptConfirmed = false;
    document.querySelector("#confirm-result").textContent = "";
    show(error.message || "復号確認ができないため、古いパスキーは無効化していません。", true);
  }
});

document.querySelector("#target-finalize").addEventListener("click", async () => {
  const migrationId = sessionStorage.getItem("wn-transfer-id") || document.querySelector("#target-id").value.trim();
  if (!decryptConfirmed) {
    show("先に新しいパスキーで既存データを復号確認してください。ログインだけでは完了できません。", true);
    return;
  }
  try {
    await api(`/api/v1/transfer/${migrationId}/finalize`, { method: "POST", body: { confirmed: true } });
    sessionStorage.removeItem("wn-transfer-token");
    decryptConfirmed = false;
    show("移行を完了しました。古いパスキーは無効です。");
  } catch (error) {
    show(error.message || "まだ完了できません。古いパスキーは無効化していません。", true);
  }
});

syncAccess().catch((error) => show(error.message, true));
if (sourceId) {
  // 開き直したときも、状態を見続ける。
  watchSource();
  refreshSource().catch((error) => show(error.message, true));
}
