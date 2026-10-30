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
const STATE_LABEL = {
  active: "有効",
  stopped: "停止",
  expired: "期限切れ",
  used: "使用済み",
};

export function viewFacts(current) {
  const state = STATE_LABEL[current && current.status] || "不明";
  const updated = current && current.updated_at ? current.updated_at : "不明";
  return {
    validity: `有効状態: ${state}`,
    updated: `共有の更新日時: ${updated}`,
  };
}

export function describeViewError(error) {
  const code = error && error.code;
  if (code === "qr_used") return "この緊急用QRは使用済みです。内容は表示できません。";
  if (code === "qr_expired") return "このQRの期限が切れています。内容は表示できません。";
  if (code === "qr_stopped") return "このQRは停止されています。内容は表示できません。";
  if (code === "qr_session_expired") return "閲覧の期限が切れています。同じ手続きでは再開できません。";
  if (code === "qr_denied") return "このQRでは閲覧を開始できません。";
  if (code === "decrypt_failed") return "復号に失敗しました。内容は表示していません。";
  if (error && error.message) return error.message;
  return "閲覧を開始できませんでした。";
}

export async function openSharedPayload(privateKey, current, shareId) {
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
      privateKey,
      decode(current.version.hpke_enc),
      decode(current.version.ciphertext),
      aad,
    );
    return JSON.parse(new TextDecoder().decode(opened));
  } catch (_error) {
    const error = new Error("復号に失敗しました。内容は表示していません。");
    error.code = "decrypt_failed";
    throw error;
  }
}

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

function showPayload(container, payload, catalog) {
  if (payload && payload.items && typeof payload.items === "object") {
    fillAnswerList(container, entriesFromPayload(payload, catalog));
    return;
  }
  container.replaceChildren();
  const row = container.ownerDocument.createElement("p");
  row.textContent = JSON.stringify(payload);
  container.append(row);
}

export function bindQrViewPage(root, {
  call = api,
  storage = sessionStorage,
  address = { hash: location.hash, pathname: location.pathname },
  navigation = history,
  decrypt = openSharedPayload,
} = {}) {
  const node = id => root.querySelector(`#${id}`);
  const status = node("status");
  const output = node("output");
  const shareId = node("viewer").dataset.shareId;
  let generation = 0;
  let ended = false;
  let busy = false;
  // このページで開始を送った試行。終了時にサーバー側で取り消す。
  let attempted = null;
  const isCurrent = ticket => !ended && ticket === generation;
  const locationLike = {
    hash: address.hash,
    replaceHash() {
      navigation.replaceState(null, "", address.pathname);
    },
  };

  function show(text) {
    status.textContent = text;
  }

  async function endOnServer() {
    try {
      await call("/api/v1/qr-view/end", { method: "POST", body: {} });
    } catch (_error) {
      /* 期限切れでも端末側の秘密は消す。 */
    }
  }

  function showFacts(current) {
    const facts = viewFacts(current);
    node("validity").textContent = facts.validity;
    node("updated").textContent = facts.updated;
  }

  async function render(current, secrets, ticket) {
    const opened = await decrypt(secrets.privateKey, current, shareId);
    if (!isCurrent(ticket)) return;
    const catalog = await call("/api/v1/answers/questions").catch(() => null);
    if (!isCurrent(ticket)) return;
    showFacts(current);
    showPayload(output, opened, catalog);
    const absolute = current.session?.absolute_expires_at || "";
    show(`閲覧中です。絶対期限 ${absolute} は延長されません。`);
  }

  async function openExplicit(secrets) {
    if (busy || ended) return;
    const ticket = ++generation;
    busy = true;
    if (secrets) attempted = { startToken: bytesToB64url(secrets.startToken), attempt: secrets.attempt };
    const openButton = node("open");
    openButton.disabled = true;
    try {
      const result = await resumeOrStartQr({ api: call, shareId, secrets, resumeOnly: false });
      if (!isCurrent(ticket)) {
        // 開始の通信中に「閲覧を終了」を押すと、その時点の終了はまだセッションが無く空振りする。
        // 遅れて開始が届いてセッションが作られたので、サーバー側でもここで終了させる。
        if (ended) void endOnServer();
        return;
      }
      storage.setItem(OPENED, shareId);
      await render(result.current, secrets, ticket);
    } catch (error) {
      if (!isCurrent(ticket)) {
        // 開始は届いた後に別の段階で失敗した場合も、作られたセッションを残さない。
        if (ended) void endOnServer();
        return;
      }
      output.replaceChildren();
      show(describeViewError(error));
    } finally {
      if (isCurrent(ticket)) {
        busy = false;
        openButton.disabled = false;
      }
    }
  }

  const secrets = captureQrSecrets(storage, locationLike, shareId);
  const initialTicket = generation;
  loadQrPage({ api: call, storage, shareId, secrets }).then(async (plan) => {
    if (!isCurrent(initialTicket)) return;
    if (plan.action === "missing") {
      show("閲覧に必要な情報がこの端末にありません。QRのリンクをこの端末で開いてください。");
      return;
    }
    if (plan.action === "resume") {
      try {
        await render(plan.result.current, secrets, initialTicket);
      } catch (error) {
        if (!isCurrent(initialTicket)) return;
        output.replaceChildren();
        show(describeViewError(error));
      }
      return;
    }
    show(plan.error ? describeViewError(plan.error) : "内容を開くを押すまで、閲覧は開始しません。");
  }).catch((error) => {
    if (isCurrent(initialTicket)) show(describeViewError(error));
  });

  node("open").addEventListener("click", () => {
    if (busy || ended) return;
    return openExplicit(readQrSecrets(storage, shareId));
  });

  node("reopen").addEventListener("click", () => {
    if (busy || ended) return;
    const next = replaceAttempt(storage, shareId);
    if (!next) {
      show("閲覧に必要な情報がこの端末にありません。");
      return;
    }
    return openExplicit(next);
  });

  node("end").addEventListener("click", async () => {
    if (ended) return;
    ended = true;
    generation += 1;
    // 開始の応答がまだでも、サーバーに試行の取り消しを残す。このあとページを閉じても、
    // 遅れて届いた開始でセッションが作られない（作られていれば失効する）。
    const cancelling = attempted
      ? call(`/api/v1/qr-shares/${shareId}/cancel`, {
        method: "POST", body: { start_token: attempted.startToken, attempt_id: attempted.attempt }, keepalive: true,
      }).catch(() => {})
      : Promise.resolve();
    clearQrSecrets(storage);
    storage.removeItem(OPENED);
    output.replaceChildren();
    node("validity").textContent = "";
    node("updated").textContent = "";
    for (const id of ["open", "reopen", "end"]) node(id).disabled = true;
    show("閲覧を終了し、この端末に置いた秘密を消しました。");
    await Promise.all([cancelling, endOnServer()]);
  });
}

function decode(value) {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) out[index] = binary.charCodeAt(index);
  return out;
}

if (typeof document !== "undefined" && document.querySelector("#viewer")) bindQrViewPage(document);
