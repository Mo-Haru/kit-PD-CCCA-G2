// 家族共有の例外的な操作を、実際のブラウザ（Chromium）・実際のサーバー・仮想パスキー（PRF付き）で試す。
// 使い方は tests/e2e/README.md。pytest には含めない（ブラウザと開発サーバーが要り、期限切れを2分待つため）。
const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright");
const OUT = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), "family-edge-"));
const BASE = process.env.E2E_BASE_URL || "http://localhost:5000";
const results = [];

function record(scenario, check, ok, detail = "") {
  results.push({ scenario, check, ok, detail });
  console.log(`${ok ? "OK  " : "NG  "}[${scenario}] ${check}${detail ? " — " + detail : ""}`);
}

async function person(browser, name) {
  const context = await browser.newContext({ viewport: { width: 420, height: 1400 } });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", ctap2Version: "ctap2_1", transport: "internal", hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: true,
  } });
  const posts = [];
  page.on("request", request => {
    if (request.method() !== "GET" && request.url().includes("/api/")) posts.push(new URL(request.url()).pathname);
  });
  page.on("pageerror", error => console.log(`[${name}] pageerror ${error.message}`));
  await page.goto(`${BASE}/auth`);
  await page.waitForFunction(() => !document.querySelector("#register-fields").disabled);
  await page.fill("#display-name", name);
  await page.check("#privacy-consent");
  await page.click("#register");
  await page.waitForURL(`${BASE}/`, { timeout: 20000 });
  return { name, context, page, posts };
}

async function openFamily(user) {
  await user.page.goto(`${BASE}/family`);
  await user.page.waitForFunction(() => !document.querySelector("#unlock-here").disabled
    || document.querySelector("#unlock-here").dataset.unlockState === "unlocked");
  if (await user.page.locator("#unlock-here").getAttribute("data-unlock-state") !== "unlocked") {
    await user.page.click("#unlock-here");
    await user.page.waitForFunction(() => document.querySelector("#unlock-here").dataset.unlockState === "unlocked", null, { timeout: 15000 });
  }
  await user.page.waitForTimeout(300);
}

async function saveAnswers(user) {
  await openFamily(user);
  await user.page.evaluate(async () => {
    const client = await import("/static/js/watashi/client.mjs");
    const catalog = await client.api("/api/v1/answers/questions");
    const items = {};
    for (const question of catalog.questions) items[question.id] = { state: "answered", value: `ダミー回答（${question.text.slice(0, 8)}）` };
    await client.saveAnswerDocument(items, 0);
  });
}

async function statusText(page) { return (await page.locator("#status").textContent()) || ""; }

async function connect(sender, recipient) {
  await openFamily(sender);
  await sender.page.click("#invite-open");
  await sender.page.waitForFunction(() => document.querySelector("#invite-link").textContent.includes("#token="));
  const link = await sender.page.locator("#invite-link").textContent();
  await recipient.page.goto(link);
  await recipient.page.waitForSelector("#invite-confirmation:not([hidden])");
  await recipient.page.waitForFunction(() => !document.querySelector("#accept-invitation").disabled);
  await recipient.page.click("#accept-invitation");
  await recipient.page.waitForFunction(() => document.querySelector("#confirmation-code").textContent.length === 9);
  const code = await recipient.page.locator("#confirmation-code").textContent();
  await openFamily(sender);
  const input = sender.page.locator("input[id^=confirm-code-]").first();
  await input.fill(code);
  await sender.page.getByRole("button", { name: "確認コードで家族に追加" }).first().click();
  await sender.page.waitForFunction(name => document.querySelector("#status").textContent.includes(`${name} さんを家族に追加しました`), recipient.name);
}

async function chooseRecipient(page, name) {
  const value = await page.locator("#connection option", { hasText: name }).getAttribute("value");
  await page.selectOption("#connection", value);
}

async function preview(page, recipientName, expiresLocal = "") {
  await page.check("input[name=scope][value=all]");
  await chooseRecipient(page, recipientName);
  await page.fill("#expires-at", expiresLocal);
  await page.waitForFunction(() => !document.querySelector("#show-preview").disabled);
  await page.click("#show-preview");
  await page.waitForFunction(() => !document.querySelector("#confirm-share").disabled);
  await page.check("#confirm-share");
}

function sharePosts(user) { return user.posts.filter(path => path === "/api/v1/family/shares" || path.endsWith("/update")).length; }

async function rotateReceiveKey(user) {
  // 受信用鍵を更新する画面はないため、画面と同じ暗号関数でAPIを呼ぶ（鍵束は保管用鍵で暗号化して保存）。
  await openFamily(user);
  return user.page.evaluate(async () => {
    const client = await import("/static/js/watashi/client.mjs");
    const crypto = await import("/static/js/watashi/crypto.mjs");
    const bytes = await import("/static/js/watashi/bytes.mjs");
    const material = client.unlockedKeyMaterial();
    const options = await client.api("/api/v1/auth/unlock/options", { method: "POST", body: {} });
    const current = (await client.api(`/api/v1/encryption-keys/${material.userId}`)).keys.find(key => key.status === "active");
    const pair = await crypto.generateHpkeKeyPair();
    const bundle = { v: 1, receive: { publicKey: bytes.bytesToB64url(pair.publicKey), privateKey: bytes.bytesToB64url(pair.privateKey) } };
    const packed = await crypto.encryptBundle({ storageKey: material.storageKey, dataKeyId: material.dataKeyId,
      revision: options.key_bundle.revision + 1, plaintext: new TextEncoder().encode(JSON.stringify(bundle)) });
    const result = await client.api("/api/v1/encryption-keys", { method: "POST", body: {
      public_key: bytes.bytesToB64url(pair.publicKey), replaces: current.id, acknowledge_key_change: true,
      key_bundle: { nonce: bytes.bytesToB64url(packed.nonce), ciphertext: bytes.bytesToB64url(packed.ciphertext), crypto_version: 1 },
    } });
    return { old: current.id, fresh: result.encryption_key_id };
  });
}

function localInMinutes(minutes) {
  const date = new Date(Date.now() + minutes * 60000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

async function cards(page, list) {
  return page.locator(`#${list} button.sharing-issued-card`).evaluateAll(nodes => nodes.map(node => ({
    state: node.dataset.shareState, text: node.querySelector(".sharing-issued-summary").textContent,
  })));
}

(async () => {
  const browser = await chromium.launch();
  const A = await person(browser, "送る人A");
  const B = await person(browser, "家族B");
  const C = await person(browser, "家族C");
  await saveAnswers(A);
  await connect(A, B);
  await connect(A, C);
  console.log("準備：A・B・Cを登録し、Aの回答を保存し、A-B・A-Cを家族に登録した");

  // ── 1. 確認後の相手・期限・鍵の変更 ──
  const S1 = "1 確認後の変更";
  await openFamily(A);
  let before = sharePosts(A);
  await preview(A.page, "家族B");
  await chooseRecipient(A.page, "家族C");
  record(S1, "確認後に相手を変える（画面操作）→ 確認が外れ、共有ボタンが押せない",
    !(await A.page.isChecked("#confirm-share")) && await A.page.isDisabled("#share"));
  await preview(A.page, "家族B");
  // 画面のイベントを通さず値だけ変わった場合（別タブの自動入力など）も、送信前の照合で止まるか。
  const cValue = await A.page.locator("#connection option", { hasText: "家族C" }).getAttribute("value");
  await A.page.evaluate(value => { document.querySelector("#connection").value = value; }, cValue);
  await A.page.click("#share");
  await A.page.waitForTimeout(800);
  record(S1, "確認後に相手の値だけ書き換わる → 送信しない", sharePosts(A) === before, await statusText(A.page));

  await preview(A.page, "家族B");
  await A.page.fill("#expires-at", localInMinutes(60 * 24));
  record(S1, "確認後に期限を変える（画面操作）→ 確認が外れ、共有ボタンが押せない",
    !(await A.page.isChecked("#confirm-share")) && await A.page.isDisabled("#share"));
  await preview(A.page, "家族B");
  await A.page.evaluate(value => { document.querySelector("#expires-at").value = value; }, localInMinutes(60 * 48));
  await A.page.click("#share");
  await A.page.waitForTimeout(800);
  record(S1, "確認後に期限の値だけ書き換わる → 送信しない", sharePosts(A) === before, await statusText(A.page));

  await preview(A.page, "家族B");
  const rotated = await rotateReceiveKey(B);
  await A.page.click("#share");
  await A.page.waitForTimeout(1000);
  record(S1, "確認後に相手（B）が受信用鍵を更新 → 古い鍵では共有しない", sharePosts(A) === before, await statusText(A.page));
  const serverRefusal = await A.page.evaluate(async ({ oldKey }) => {
    const client = await import("/static/js/watashi/client.mjs");
    const connection = JSON.parse([...document.querySelectorAll("#connection option")].find(o => o.textContent === "家族B").value);
    const catalog = await client.api("/api/v1/answers/questions");
    const items = { [catalog.questions[0].id]: { state: "answered", value: "古い鍵向けの内容" } };
    try {
      await client.createFamilyShare({ connectionId: connection.id, recipientUserId: connection.userId, recipientKeyId: oldKey,
        selection: { scope: "all" }, payload: { schema: 1, items }, expiresAt: null });
      return "accepted";
    } catch (error) { return error.code || error.message; }
  }, { oldKey: rotated.old });
  record(S1, "画面を通さず古い鍵IDで共有を作ろうとしても、作られない", serverRefusal !== "accepted", serverRefusal);
  await A.page.screenshot({ path: `${OUT}/1-after-key-change.png`, fullPage: true });

  // ── 3. 鍵更新後の再共有 ──（先に有効な共有を作り、その後で相手が鍵を更新する）
  const S3 = "3 鍵更新後の再共有";
  await openFamily(A);
  await preview(A.page, "家族B");
  await A.page.click("#share");
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("共有しました"));
  await openFamily(B);
  let received = await cards(B.page, "received");
  record(S3, "共有直後、Bは受け取った共有を開ける（有効）", received.some(card => card.state === "active"), JSON.stringify(received));
  await rotateReceiveKey(B);
  await openFamily(A);
  const ownAfterRotate = await cards(A.page, "shares");
  record(S3, "Bの鍵更新後、Aの共有は「再共有が必要」", ownAfterRotate.some(card => card.state === "key_pending"), JSON.stringify(ownAfterRotate));
  await openFamily(B);
  received = await cards(B.page, "received");
  record(S3, "Bの一覧でも「再共有が必要」", received.some(card => card.state === "key_pending"), JSON.stringify(received));
  await B.page.locator("#received button.sharing-issued-card").first().click();
  await B.page.waitForTimeout(1200);
  const pendingDetail = (await B.page.locator("#family-detail").textContent()) || "";
  record(S3, "Bが開いても内容は出ない（暗号文を受け取らない）", !pendingDetail.includes("ダミー回答"),
    (await B.page.locator("#family-detail-status").textContent()) || "");
  await B.page.click("#family-close-top");
  // Aが現在の鍵で確認し直して更新する。
  await openFamily(A);
  await preview(A.page, "家族B");
  await A.page.locator("#shares button.sharing-issued-card[data-share-state=key_pending]").first().click();
  await A.page.waitForSelector("#family-detail[open]");
  await A.page.waitForFunction(() => document.querySelector("#family-detail-body").textContent.includes("版"), null, { timeout: 10000 })
    .catch(() => {});
  const detailText = (await A.page.locator("#family-detail-body").textContent()) || "";
  record(S3, "Aの詳細に「確認した現在の鍵で更新してください」と出る", detailText.includes("確認した現在の鍵で更新"));
  await A.page.getByRole("button", { name: "この範囲で更新" }).click();
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("更新しました"), null, { timeout: 10000 })
    .catch(() => {});
  record(S3, "Aが現在の鍵で更新できる", (await statusText(A.page)).includes("更新しました"), await statusText(A.page));
  await A.page.keyboard.press("Escape");
  await openFamily(B);
  received = await cards(B.page, "received");
  await B.page.locator("#received button.sharing-issued-card[data-share-state=active]").first().click();
  await B.page.waitForFunction(() => document.querySelector("#family-detail-body").textContent.includes("ダミー回答"), null, { timeout: 10000 })
    .catch(() => {});
  record(S3, "Bが新しい鍵で復号して内容を見られる", ((await B.page.locator("#family-detail-body").textContent()) || "").includes("ダミー回答"));
  await B.page.screenshot({ path: `${OUT}/3-reshared.png`, fullPage: true });
  await B.page.click("#family-close-top");

  // ── 4. 通信が遅いときの閲覧終了 ──
  const S4 = "4 通信が遅いときの閲覧終了";
  // (a) 家族共有の詳細：読み込み中に閉じたら、遅れて届いた内容を出さない。
  await openFamily(B);
  await B.page.route("**/api/v1/family/received/*", async route => { await new Promise(r => setTimeout(r, 2500)); await route.continue(); });
  await B.page.locator("#received button.sharing-issued-card[data-share-state=active]").first().click();
  await B.page.waitForTimeout(300);
  await B.page.click("#family-close-top");
  await B.page.waitForTimeout(3500);
  const lateBody = (await B.page.locator("#family-detail-body").textContent()) || "";
  record(S4, "家族共有：読み込み中に閉じると、遅れて届いた回答を表示しない",
    !lateBody.includes("ダミー回答") && !(await B.page.locator("#family-detail").evaluate(node => node.open)));
  await B.page.unroute("**/api/v1/family/received/*");
  // (b) QR閲覧：開始の通信が遅いあいだに「閲覧を終了」を押す。
  await A.page.goto(`${BASE}/sharing`);
  await A.page.waitForFunction(() => !document.querySelector("#unlock-here").disabled);
  await A.page.click("#unlock-here");
  await A.page.waitForFunction(() => document.querySelector("#unlock-here").dataset.unlockState === "unlocked");
  await A.page.check("input[name=scope][value=all]");
  await A.page.check("input[name=mode][value=timed]");
  await A.page.dispatchEvent("input[name=mode][value=timed]", "change");
  await A.page.fill("#expires-at", localInMinutes(60));
  await A.page.click("#show-preview");
  await A.page.waitForFunction(() => !document.querySelector("#confirm-share").disabled);
  await A.page.check("#confirm-share");
  await A.page.dispatchEvent("#confirm-share", "change");
  await A.page.click("#create-qr");
  await A.page.waitForFunction(() => document.querySelector("#share-url").value.includes("#k="));
  const qrUrl = await A.page.locator("#share-url").inputValue();
  const viewer = await browser.newContext();
  const view = await viewer.newPage();
  await view.route(/\/api\/v1\/(qr-shares\/.*\/start|qr-view\/current)/, async route => {
    await new Promise(r => setTimeout(r, 2500)); await route.continue();
  });
  await view.goto(qrUrl);
  await view.waitForTimeout(500);
  await view.click("#open");
  await view.waitForTimeout(300);
  await view.click("#end");
  await view.waitForTimeout(6500);
  const viewOutput = (await view.locator("#output").textContent()) || "";
  const viewStatus = (await view.locator("#status").textContent()) || "";
  const storageLeft = await view.evaluate(() => Object.keys(sessionStorage));
  record(S4, "QR閲覧：開始の通信中に終了すると、遅れて届いても内容を出さない", !viewOutput.includes("ダミー回答"), viewStatus);
  record(S4, "QR閲覧：終了後、端末に秘密が残らない", storageLeft.length === 0, JSON.stringify(storageLeft));
  record(S4, "QR閲覧：アドレスバーに秘密が残らない", !view.url().includes("#"), view.url());
  await view.unroute(/./);
  const orphan = await view.evaluate(async () => (await fetch("/api/v1/qr-view/current")).status);
  record(S4, "QR閲覧：終了後にサーバーの閲覧セッションが残らない", orphan === 401, `GET /qr-view/current → ${orphan}`);
  await view.screenshot({ path: `${OUT}/4-qr-ended.png`, fullPage: true });

  // ── 2. 期限切れ ──（期限を2分後にした共有を作り、期限を過ぎるまで実際に待つ）
  const S2 = "2 期限切れ";
  await openFamily(A);
  const expiresLocal = localInMinutes(2);
  await preview(A.page, "家族C", expiresLocal);
  await A.page.click("#share");
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("共有しました"));
  await openFamily(C);
  let cReceived = await cards(C.page, "received");
  record(S2, "期限内は、Cが受け取った共有を開ける", cReceived.some(card => card.state === "active"), JSON.stringify(cReceived));
  const shareId = await C.page.evaluate(async () => (await (await fetch("/api/v1/family/received")).json()).shares[0].id);
  const deadline = new Date(expiresLocal + ":00Z").getTime();
  const wait = Math.max(0, deadline - Date.now()) + 5000;
  console.log(`期限（${expiresLocal}）まで ${Math.round(wait / 1000)} 秒待つ`);
  await C.page.waitForTimeout(wait);
  const fetched = await C.page.evaluate(async id => (await fetch(`/api/v1/family/received/${id}`)).status, shareId);
  record(S2, "期限を過ぎると、Cは内容を取得できない", fetched === 404, `GET /family/received/<id> → ${fetched}`);
  await openFamily(C);
  cReceived = await cards(C.page, "received");
  record(S2, "Cの一覧で、期限切れの共有は内容を開けない状態", !cReceived.some(card => card.state === "active"), JSON.stringify(cReceived));
  await openFamily(A);
  const aShares = await cards(A.page, "shares");
  record(S2, "Aの一覧で「期限切れ」と表示される", aShares.some(card => card.state === "expired"), JSON.stringify(aShares.map(card => card.state)));
  await A.page.screenshot({ path: `${OUT}/2-expired-sender.png`, fullPage: true });

  // ── 5. 招待リンクの切り替え ──
  const S5 = "5 招待リンクの切り替え";
  const D = await person(browser, "申請者D");
  await openFamily(A);
  await A.page.click("#invite-open");
  await A.page.waitForFunction(() => document.querySelector("#invite-link").textContent.includes("#token="));
  const link1 = await A.page.locator("#invite-link").textContent();
  await A.page.click("#invite-open");
  await A.page.waitForFunction(first => document.querySelector("#invite-link").textContent !== first, link1);
  const link2 = await A.page.locator("#invite-link").textContent();
  await D.page.goto(link1);
  await D.page.waitForSelector("#invite-confirmation:not([hidden])");
  const before5 = D.posts.length;
  await D.page.evaluate(next => { location.hash = new URL(next).hash; }, link2);
  await D.page.waitForTimeout(500);
  record(S5, "招待を開いたまま別の招待リンクへ切り替える → 承諾ボタンが止まる",
    await D.page.isDisabled("#accept-invitation"), (await D.page.locator("#invite-status").textContent()) || "");
  record(S5, "切り替え後は承諾の確認欄が隠れる", await D.page.locator("#invite-confirmation").isHidden());
  // 隠れた・無効のボタンを、画面のスクリプトから直接押しても送らないか。
  await D.page.evaluate(() => document.querySelector("#accept-invitation").click());
  await D.page.waitForTimeout(500);
  record(S5, "切り替え後に押しても申請を送らない", D.posts.length === before5);
  await D.page.reload();
  await D.page.waitForFunction(() => !document.querySelector("#accept-invitation").disabled);
  await D.page.click("#accept-invitation");
  await D.page.waitForFunction(() => document.querySelector("#confirmation-code").textContent.length === 9);
  const requested = D.posts.filter(path => path === "/api/v1/family/invitations/request").length;
  record(S5, "再読み込みすると、新しいリンクで1回だけ申請できる", requested === 1);
  const dLink = await D.page.locator("#confirmation-link").textContent();
  const E = await person(browser, "申請者E");
  await E.page.goto(link1);
  await E.page.waitForFunction(() => !document.querySelector("#accept-invitation").disabled);
  await E.page.click("#accept-invitation");
  await E.page.waitForFunction(() => document.querySelector("#confirmation-code").textContent.length === 9);
  const eLink = await E.page.locator("#confirmation-link").textContent();
  // 招待した人が確認リンクを開いたまま、別の申請者の確認リンクへ切り替える。
  const beforeConfirm = A.posts.length;
  await A.page.goto(dLink);
  await A.page.waitForFunction(() => !document.querySelector("#confirm-link").disabled);
  await A.page.evaluate(next => { location.hash = new URL(next).hash; }, eLink);
  await A.page.waitForTimeout(500);
  record(S5, "確認リンクを開いたまま別の申請者のリンクへ切り替える → 承認ボタンが止まる",
    await A.page.isDisabled("#confirm-link"), (await A.page.locator("#confirm-status").textContent()) || "");
  await A.page.evaluate(() => document.querySelector("#confirm-link").click());
  await A.page.waitForTimeout(800);
  record(S5, "切り替え後に押しても承認を送らない", !A.posts.slice(beforeConfirm).includes("/api/v1/family/invitations/confirm-link"));
  await A.page.reload();
  await A.page.waitForFunction(() => !document.querySelector("#confirm-link").disabled);
  await A.page.click("#confirm-link");
  await A.page.waitForFunction(() => document.querySelector("#confirm-status").textContent.includes("家族に追加しました"), null, { timeout: 15000 })
    .catch(() => {});
  const approved = (await A.page.locator("#confirm-status").textContent()) || "";
  record(S5, "再読み込みすると、今開いているリンクの申請者（E）だけを承認する", approved.includes("申請者E"), approved);
  await A.page.screenshot({ path: `${OUT}/5-confirm-switched.png`, fullPage: true });
  await openFamily(A);
  const familyNames = await A.page.locator("#connections").textContent();
  record(S5, "Dは承認されておらず、家族に入っていない", !familyNames.includes("申請者D") && familyNames.includes("申請者E"), familyNames.replace(/\s+/g, " "));

  fs.writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  console.log(`結果と画面の記録: ${OUT}`);
  const failed = results.filter(item => !item.ok);
  console.log(`\n${results.length - failed.length}/${results.length} 件 OK`);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch(error => { console.error("SCRIPT ERROR", error); process.exit(2); });
