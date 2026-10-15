import { b64urlToBytes, bytesToB64url } from "./bytes.mjs";
import { api, registerTransferredPasskey, unlockedKeyMaterial } from "./client.mjs";
import { generateHpkeKeyPair, hpkeAad, hpkeOpen, hpkeSeal } from "./crypto.mjs";

const status = document.querySelector("#status");
let sourceId = "";
let targetPrivate = null;
let transferredStorageKey = null;
let timer = 0;

function show(text) {
  status.textContent = text;
}

async function fingerprintOf(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("").slice(0, 16);
  return hex.match(/.{4}/g).join(" ");
}

async function refreshSource() {
  if (!sourceId) return;
  const migration = (await api(`/api/v1/transfer/${sourceId}`)).migration;
  document.querySelector("#source-state").textContent = `${migration.status} / 期限 ${migration.expires_at}`;
  document.querySelector("#source-code").textContent = migration.confirmation_code || "まだありません";
  document.querySelector("#source-fingerprint").textContent = migration.fingerprint || "まだありません";
  if (migration.status === "source_approved") await uploadBundle(migration);
}

async function uploadBundle(migration) {
  const material = unlockedKeyMaterial();
  if (!material || !migration.target_public_key) {
    show("鍵束を送るには、認証画面で鍵を解除してから、この画面へ戻ってください。");
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
  show("鍵束を送りました。古いパスキーは、新しい端末が復号とパスキー登録を終えるまで有効です。");
  window.clearInterval(timer);
}

document.querySelector("#source-start").addEventListener("click", async () => {
  try {
    const started = await api("/api/v1/transfer/start", { method: "POST", body: {} });
    sourceId = started.migration.id;
    document.querySelector("#source-id").textContent = sourceId;
    show("移行を開始しました。新しい端末にこのIDを入力してください。");
    window.clearInterval(timer);
    timer = window.setInterval(() => {
      refreshSource().catch((error) => show(error.message));
    }, 2000);
    await refreshSource();
  } catch (error) {
    show(error.message);
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
    show(error.message);
  }
});

document.querySelector("#source-cancel").addEventListener("click", async () => {
  try {
    await api(`/api/v1/transfer/${sourceId}/cancel`, { method: "POST", body: {} });
    window.clearInterval(timer);
    show("移行を取り消しました。古いパスキーはそのまま使えます。");
  } catch (error) {
    show(error.message);
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
      show("表示された指紋がこの端末の鍵と一致しません。承認しないでください。");
      return;
    }
    show("コードと指紋がこの端末の鍵と一致しています。古い端末の表示と比べてください。");
  } catch (error) {
    show(error.message);
  }
});

document.querySelector("#target-finish").addEventListener("click", async () => {
  const transferToken = sessionStorage.getItem("wn-transfer-token");
  const migrationId = sessionStorage.getItem("wn-transfer-id");
  if (!transferToken || !targetPrivate || !migrationId) {
    show("先にこの端末の鍵を登録してください。");
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
    transferredStorageKey = b64urlToBytes(opened.storageKey);
    await registerTransferredPasskey(transferToken, transferredStorageKey);
    transferredStorageKey = null;
    show("新しいパスキーを登録しました。認証画面でそのパスキーによりログインしてから、移行完了を押すと古いパスキーが無効になります。");
  } catch (error) {
    show(error.message || "新しい端末での確認がまだできません。古いパスキーは無効化していません。");
  }
});

document.querySelector("#target-finalize").addEventListener("click", async () => {
  const migrationId = sessionStorage.getItem("wn-transfer-id") || document.querySelector("#target-id").value.trim();
  try {
    await api(`/api/v1/transfer/${migrationId}/finalize`, { method: "POST", body: { confirmed: true } });
    sessionStorage.removeItem("wn-transfer-token");
    show("移行を完了しました。古いパスキーは無効です。");
  } catch (error) {
    show(error.message || "まだ完了できません。新しいパスキーでログインしているか確認してください。");
  }
});
