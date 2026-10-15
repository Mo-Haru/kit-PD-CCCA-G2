import { bytesToB64url, randomBytes } from "./bytes.mjs";
import { api } from "./client.mjs";
import {
  clearQrSecrets,
  entriesFromPayload,
  fillAnswerList,
  holdQrSecrets,
  hpkeAad,
  hpkeOpen,
  parseShareFragment,
  readQrSecrets,
} from "./crypto.mjs";

const OPENED = "wn-qr-opened";

export function captureQrSecrets(storage, locationLike, shareId) {
  const parsed = parseShareFragment(locationLike.hash);
  if (parsed) {
    const secrets = { ...parsed, attempt: bytesToB64url(randomBytes(32)) };
    holdQrSecrets(storage, shareId, secrets);
    locationLike.hash = "";
    if (typeof locationLike.replaceHash === "function") locationLike.replaceHash();
    return secrets;
  }
  return readQrSecrets(storage, shareId);
}

export function replaceAttempt(storage, shareId) {
  const secrets = readQrSecrets(storage, shareId);
  if (!secrets) return null;
  const next = { ...secrets, attempt: bytesToB64url(randomBytes(32)) };
  holdQrSecrets(storage, shareId, next);
  return next;
}

export async function resumeOrStartQr({ api: call, shareId, secrets, resumeOnly }) {
  if (!secrets) {
    const error = new Error("閲覧に必要な情報がこの端末にありません。QRのリンクをこの端末で開いてください。");
    error.code = "missing_secret";
    throw error;
  }
  let current = null;
  try {
    current = await call("/api/v1/qr-view/current");
  } catch (error) {
    if (resumeOnly || error.status !== 401) throw error;
  }
  if (current && current.id === shareId) return { current, started: null };
  if (current && current.id !== shareId && resumeOnly) {
    const error = new Error("別の共有の閲覧セッションです。このQRの内容は開いていません。");
    error.code = "wrong_share";
    throw error;
  }
  if (resumeOnly) {
    const error = new Error("まだ内容を開いていません。");
    error.code = "not_started";
    throw error;
  }
  const started = await call(`/api/v1/qr-shares/${shareId}/start`, {
    method: "POST",
    body: { start_token: bytesToB64url(secrets.startToken), attempt_id: secrets.attempt },
  });
  current = await call("/api/v1/qr-view/current");
  if (!current || current.id !== shareId) {
    const error = new Error("別の共有の閲覧セッションです。");
    error.code = "wrong_share";
    throw error;
  }
  return { current, started };
}

export async function loadQrPage({ api: call, storage, shareId, secrets }) {
  if (!secrets) return { action: "missing" };
  if (storage.getItem(OPENED) !== shareId) return { action: "wait" };
  try {
    const result = await resumeOrStartQr({ api: call, shareId, secrets, resumeOnly: true });
    return { action: "resume", result };
  } catch (error) {
    return { action: "wait", error };
  }
}

function showPayload(container, payload) {
  if (payload && payload.items && typeof payload.items === "object") {
    fillAnswerList(container, entriesFromPayload(payload, null));
    return;
  }
  container.replaceChildren();
  const row = container.ownerDocument.createElement("p");
  row.textContent = JSON.stringify(payload);
  container.append(row);
}

function bindPage() {
  const status = document.querySelector("#status");
  const output = document.querySelector("#output");
  const shareId = document.querySelector("#viewer").dataset.shareId;
  const locationLike = {
    hash: location.hash,
    replaceHash() {
      history.replaceState(null, "", location.pathname);
    },
  };

  function show(text) {
    status.textContent = text;
  }

  async function render(current, secrets) {
    const aad = hpkeAad({
      purpose: "qr",
      contextId: shareId,
      ownerId: current.owner_user_id,
      recipientId: "qr",
      keyId: current.public_key,
      version: current.current_version,
    });
    const opened = await hpkeOpen(
      secrets.privateKey,
      decode(current.version.hpke_enc),
      decode(current.version.ciphertext),
      aad,
    );
    showPayload(output, JSON.parse(new TextDecoder().decode(opened)));
    const absolute = current.session?.absolute_expires_at || "";
    show(`閲覧中です。絶対期限 ${absolute} は延長されません。`);
  }

  async function openExplicit(secrets) {
    const result = await resumeOrStartQr({ api, shareId, secrets, resumeOnly: false });
    sessionStorage.setItem(OPENED, shareId);
    await render(result.current, secrets);
  }

  const secrets = captureQrSecrets(sessionStorage, locationLike, shareId);
  loadQrPage({ api, storage: sessionStorage, shareId, secrets }).then(async (plan) => {
    if (plan.action === "missing") {
      show("閲覧に必要な情報がこの端末にありません。QRのリンクをこの端末で開いてください。");
      return;
    }
    if (plan.action === "resume") {
      await render(plan.result.current, secrets);
      return;
    }
    show(plan.error && plan.error.message ? plan.error.message : "内容を開くを押すまで、閲覧は開始しません。");
  }).catch((error) => show(error.message || "閲覧を開始できませんでした。"));

  document.querySelector("#open").addEventListener("click", async () => {
    const currentSecrets = readQrSecrets(sessionStorage, shareId);
    try {
      await openExplicit(currentSecrets);
    } catch (error) {
      show(error.message || "閲覧を開始できませんでした。");
    }
  });

  document.querySelector("#reopen").addEventListener("click", async () => {
    const next = replaceAttempt(sessionStorage, shareId);
    if (!next) {
      show("閲覧に必要な情報がこの端末にありません。");
      return;
    }
    try {
      await openExplicit(next);
    } catch (error) {
      show(error.message || "新しい閲覧を開始できませんでした。緊急用QRは一度使うと再開できません。");
    }
  });

  document.querySelector("#end").addEventListener("click", async () => {
    try {
      await api("/api/v1/qr-view/end", { method: "POST", body: {} });
    } catch (_error) {
      /* 期限切れでも端末側の秘密は消す。 */
    }
    clearQrSecrets(sessionStorage);
    sessionStorage.removeItem(OPENED);
    output.replaceChildren();
    show("閲覧を終了し、この端末に置いた秘密を消しました。");
  });
}

function decode(value) {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) out[index] = binary.charCodeAt(index);
  return out;
}

if (typeof document !== "undefined" && document.querySelector("#viewer")) bindPage();
