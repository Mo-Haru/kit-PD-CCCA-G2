import { bytesToB64url, randomBytes } from "./bytes.mjs";
import { api } from "./client.mjs";
import {
  clearQrSecrets,
  holdQrSecrets,
  hpkeAad,
  hpkeOpen,
  parseShareFragment,
  readQrSecrets,
} from "./crypto.mjs";

const status = document.querySelector("#status");
const output = document.querySelector("#output");
const shareId = document.querySelector("#viewer").dataset.shareId;

function show(text) {
  status.textContent = text;
}

function secretsFromPage() {
  const parsed = parseShareFragment(location.hash);
  if (parsed) {
    const attempt = bytesToB64url(randomBytes(32));
    const secrets = { ...parsed, attempt };
    holdQrSecrets(sessionStorage, shareId, secrets);
    history.replaceState(null, "", location.pathname);
    return secrets;
  }
  return readQrSecrets(sessionStorage, shareId);
}

async function openView() {
  const secrets = secretsFromPage();
  if (!secrets) {
    show("閲覧に必要な情報がこの端末にありません。QRのリンクをこの端末で開いてください。");
    return;
  }
  const started = await api(`/api/v1/qr-shares/${shareId}/start`, {
    method: "POST",
    body: { start_token: bytesToB64url(secrets.startToken), attempt_id: secrets.attempt },
  });
  const current = await api("/api/v1/qr-view/current");
  const aad = hpkeAad({
    purpose: "qr",
    contextId: shareId,
    ownerId: current.owner_user_id,
    recipientId: "qr",
    keyId: current.public_key,
    version: current.current_version,
  });
  try {
    const opened = await hpkeOpen(
      secrets.privateKey,
      decode(current.version.hpke_enc),
      decode(current.version.ciphertext),
      aad,
    );
    output.textContent = new TextDecoder().decode(opened);
    show(`閲覧中です。絶対期限 ${started.absolute_expires_at} は延長されません。`);
  } catch (_error) {
    show("復号できませんでした。リンクの鍵と共有データが一致していません。");
  }
}

function decode(value) {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) out[index] = binary.charCodeAt(index);
  return out;
}

document.querySelector("#end").addEventListener("click", async () => {
  try {
    await api("/api/v1/qr-view/end", { method: "POST", body: {} });
  } catch (_error) {
    /* 期限切れでも端末側の秘密は消す。 */
  }
  clearQrSecrets(sessionStorage);
  output.textContent = "";
  show("閲覧を終了し、この端末に置いた秘密を消しました。");
});

openView().catch((error) => show(error.message || "閲覧を開始できませんでした。"));
