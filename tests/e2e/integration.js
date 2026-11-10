// 統合した機能（回答の画面・わたしノート・QRでの共有・LINEの受信口）を、
// 実際のブラウザ（Chromium）・実際のサーバー・仮想パスキー（PRF付き）で通して試す。
// 使い方は tests/e2e/README.md。pytest には含めない（ブラウザと開発サーバーが要るため）。
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright");
const OUT = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), "integration-"));
const DB_FILE = process.env.E2E_DB_FILE || "";
const BASE = process.env.E2E_BASE_URL || "http://localhost:5000";
const LINE_SECRET = "e2e-dummy-secret";
const TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;
const results = [];

function record(scenario, check, ok, detail = "") {
  results.push({ scenario, check, ok, detail });
  console.log(`${ok ? "OK  " : "NG  "}[${scenario}] ${check}${detail ? " — " + detail : ""}`);
}

async function person(browser, name) {
  const context = await browser.newContext({ viewport: { width: 420, height: 1400 }, timezoneId: TIMEZONE });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", ctap2Version: "ctap2_1", transport: "internal", hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: true,
  } });
  const errors = [];
  page.on("pageerror", error => { errors.push(error.message); console.log(`[${name}] pageerror ${error.message}`); });
  await page.goto(`${BASE}/auth`);
  await page.waitForFunction(() => !document.querySelector("#register-fields").disabled);
  await page.fill("#display-name", name);
  await page.check("#privacy-consent");
  await page.click("#register");
  await page.waitForURL(`${BASE}/`, { timeout: 20000 });
  return { name, context, page, errors };
}

async function statusText(page) {
  return page.evaluate(() => {
    const alert = document.querySelector("#site-alert");
    if (alert && !alert.hidden) return document.querySelector("#site-alert-text").textContent || "";
    return document.querySelector("#status")?.textContent || "";
  });
}

// 回答の画面を開く。開くと自動で解除し、保存済みの回答を読み込むまで待つ。
async function openAnswers(page, hash = "") {
  await page.goto(`${BASE}/answers${hash}`);
  await page.waitForFunction(() => !document.querySelector("#acp-fields").disabled
    || !document.querySelector("#site-alert").hidden, null, { timeout: 15000 });
}

async function choose(page, number, label) {
  const card = page.locator("[data-acp-question]").nth(number - 1);
  await card.locator(".answer-option", { hasText: label }).click();
}

async function openNote(page) {
  await page.goto(`${BASE}/note`);
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("表示しています")
    || document.querySelector("#status").textContent.includes("ありません")
    || !document.querySelector("#site-alert").hidden, null, { timeout: 15000 });
}

function localInMinutes(minutes) {
  const date = new Date(Date.now() + minutes * 60000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function today() {
  const now = new Date();
  return `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日`;
}

async function lineCallback(body, signature) {
  const raw = JSON.stringify(body);
  const headers = { "Content-Type": "application/json" };
  if (signature !== null) {
    headers["X-Line-Signature"] = signature ?? crypto.createHmac("sha256", LINE_SECRET).update(raw).digest("base64");
  }
  return (await fetch(`${BASE}/line/callback`, { method: "POST", headers, body: raw })).status;
}

(async () => {
  const browser = await chromium.launch();
  const A = await person(browser, "統合テストA");

  // ── 1. 回答の画面（今牧担当） ──
  const S1 = "1 回答の画面";
  await openAnswers(A.page);
  record(S1, "開くと自動で解除し、入力できる", !(await A.page.isDisabled("#acp-fields")), await statusText(A.page));
  const counts = await A.page.evaluate(() => ({
    questions: document.querySelectorAll("[data-acp-question]").length,
    page1: document.querySelectorAll("#page1 [data-acp-question]").length,
    page2Hidden: document.querySelector("#page2").hidden,
  }));
  record(S1, "質問22問（選択式21問＋自由記述）、1ページ目は10問で2ページ目は隠れている",
    counts.questions === 22 && counts.page1 === 10 && counts.page2Hidden, JSON.stringify(counts));
  await A.page.click("#openExplanation");
  record(S1, "「人生会議（ACP）とは？」で説明が開く", await A.page.isVisible("#acpExplanation"));
  await A.page.click("#closeExplanation");
  await choose(A.page, 1, "住み慣れた自宅で暮らしたい");
  await choose(A.page, 2, "趣味や好きなことを楽しむとき");
  await choose(A.page, 2, "一人でゆっくり過ごすとき");
  const second = await A.page.locator("[data-acp-question]").nth(1).locator(".answer-checkbox:checked").count();
  record(S1, "1つの質問では1つだけ選べる（選び直すと前の選択が外れる）", second === 1);
  // 質問9（1ページ目）の注釈。
  await A.page.locator("[data-acp-term]").first().click();
  const annotation = await A.page.locator(".annotation:visible").first().textContent();
  record(S1, "質問の注釈（ケアマネジャーとは？）を押すと説明が出る", (annotation || "").includes("専門職"), (annotation || "").slice(0, 30));
  await A.page.click("#nextPage");
  await A.page.waitForSelector("#page2:not([hidden])");
  record(S1, "「次へ」で2ページ目（質問11〜21）に切り替わる", await A.page.isHidden("#page1"));
  // 本文は .site-content の中でスクロールする。ページを切り替えたら、新しいページの先頭が見える位置にする。
  await A.page.waitForTimeout(500);
  const page2Top = await A.page.evaluate(() => {
    const top = document.querySelector("#page2").getBoundingClientRect().top;
    return { top: Math.round(top), height: window.innerHeight };
  });
  record(S1, "「次へ」のあと、2ページ目の先頭が画面内に見える", page2Top.top >= 0 && page2Top.top < page2Top.height / 2, JSON.stringify(page2Top));
  await choose(A.page, 11, "家族");
  await A.page.fill("#freeText", "テスト用の自由記述です");
  await A.page.click("#saveAnswers");
  await A.page.waitForFunction(() => document.querySelector("#saveMessage").textContent === "回答を保存しました。", null, { timeout: 10000 });
  record(S1, "途中保存で暗号化して保存できる", true, await statusText(A.page));
  const storage = await A.page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  record(S1, "ブラウザの保存領域に回答の文が残らない", !storage.includes("自宅で暮らしたい") && !storage.includes("自由記述"), storage.slice(0, 120));
  await A.page.click("#checkAnswers");
  const summary = (await A.page.locator("#answerMessage").textContent()) || "";
  record(S1, "回答を確認すると、選んだ回答と未回答が分野ごとに出る",
    summary.includes("住み慣れた自宅で暮らしたい") && summary.includes("未回答") && summary.includes("自分の価値観について"));
  await openAnswers(A.page);
  const restored = await A.page.evaluate(() => ({
    first: document.querySelector("[data-acp-question] .answer-checkbox:checked")?.dataset.answerLabel,
    free: document.querySelector("#freeText").value,
  }));
  record(S1, "開き直すと、保存した回答を復号して入力欄に戻す",
    restored.first === "住み慣れた自宅で暮らしたい" && restored.free === "テスト用の自由記述です", JSON.stringify(restored));
  if (DB_FILE) {
    const raw = fs.readFileSync(DB_FILE);
    // 選択肢の文は質問としてDBにあるので、回答だけにある自由記述で確かめる。
    record(S1, "サーバーのDBに回答の平文がない（自由記述）", !raw.includes(Buffer.from("テスト用の自由記述")));
  }
  await A.page.screenshot({ path: `${OUT}/1-answers-viewport.png` });
  await A.page.screenshot({ path: `${OUT}/1-answers.png`, fullPage: true });

  // ── 2. わたしノート（沖担当） ──
  const S2 = "2 わたしノート";
  await openNote(A.page);
  const progress = await A.page.evaluate(() => ({
    percent: document.querySelector("#note-percent").textContent,
    now: document.querySelector("#note-bar").getAttribute("aria-valuenow"),
    text: document.querySelector("#note-progress-text").textContent,
    width: document.querySelector("#note-bar-fill").style.width,
  }));
  record(S2, "進み具合：22問中4問回答で18%", progress.percent === "18" && progress.now === "18" && progress.width === "18%"
    && progress.text.includes("22問中4問に回答済み"), JSON.stringify(progress));
  const firstItem = A.page.locator(".note-item").first();
  const firstText = (await firstItem.textContent()) || "";
  record(S2, "回答済みの質問に、答えと回答日が出る", firstText.includes("住み慣れた自宅で暮らしたい") && firstText.includes(`${today()}に回答`), firstText.replace(/\s+/g, " ").slice(0, 80));
  const unanswered = (await A.page.locator(".note-item").nth(2).textContent()) || "";
  record(S2, "未回答の質問は「まだ回答していません」と「回答する」", unanswered.includes("まだ回答していません") && unanswered.includes("回答する"));
  await A.page.waitForTimeout(1200); // 進捗バーが伸びる動き（0.9秒）を待つ
  await A.page.screenshot({ path: `${OUT}/2-note.png`, fullPage: true });
  await A.page.locator(".note-item").nth(10).getByRole("link", { name: "書き直す" }).click();
  await A.page.waitForFunction(() => !document.querySelector("#acp-fields").disabled, null, { timeout: 15000 });
  const landed = await A.page.evaluate(() => {
    const target = document.getElementById(location.hash.slice(1));
    return { page2: !document.querySelector("#page2").hidden, inPage2: document.querySelector("#page2").contains(target) };
  });
  record(S2, "「書き直す」で回答の画面の、その質問のあるページ（2ページ目）を開く", landed.page2 && landed.inPage2, JSON.stringify(landed));
  await openNote(A.page);
  await A.page.locator(".note-item").last().getByRole("link", { name: "書き直す" }).click();
  await A.page.waitForFunction(() => !document.querySelector("#acp-fields").disabled, null, { timeout: 15000 });
  await A.page.waitForTimeout(300);
  const freeLanded = await A.page.evaluate(() => {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    const box = document.querySelector("#freeText").getBoundingClientRect();
    return { found: Boolean(target && target.contains(document.querySelector("#freeText"))), top: Math.round(box.top), height: window.innerHeight };
  });
  record(S2, "自由記述の「書き直す」で、回答の画面の自由記述の欄を開く",
    freeLanded.found && freeLanded.top >= 0 && freeLanded.top < freeLanded.height, JSON.stringify(freeLanded));

  // 未ログインで質問を示すURL（#q-…）を開き、画面のログインボタンから戻ると、その質問を開く。
  const guest = await browser.newContext({ viewport: { width: 420, height: 1400 }, timezoneId: TIMEZONE });
  const guestPage = await guest.newPage();
  const questionHash = await A.page.evaluate(() => `#q-${document.querySelectorAll("[data-acp-question]")[10].dataset.questionId}`);
  await guestPage.goto(`${BASE}/answers${questionHash}`);
  await guestPage.waitForSelector("#login-here:not([hidden])");
  await guestPage.waitForTimeout(300);
  const loginHref = await guestPage.locator("#login-here").getAttribute("href");
  const loginUrl = new URL(loginHref, BASE);
  const returned = new URLSearchParams(loginUrl.hash.slice(1)).get("return-fragment");
  record(S2, "未ログイン時の画面のログインボタンが、質問を示すURLの#以降を引き継ぐ",
    loginUrl.searchParams.get("next") === "/answers" && `#${returned}` === questionHash, loginHref);
  const cdp = await guest.newCDPSession(guestPage);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", ctap2Version: "ctap2_1", transport: "internal", hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: true,
  } });
  await guestPage.click("#login-here");
  await guestPage.waitForFunction(() => !document.querySelector("#register-fields").disabled);
  await guestPage.fill("#display-name", "統合テストB");
  await guestPage.check("#privacy-consent");
  await guestPage.click("#register");
  await guestPage.waitForURL(/\/answers#q-/, { timeout: 20000 }).catch(() => {});
  await guestPage.waitForFunction(() => !document.querySelector("#acp-fields").disabled, null, { timeout: 15000 }).catch(() => {});
  const back = await guestPage.evaluate(() => ({
    url: location.pathname + location.hash,
    page2: !document.querySelector("#page2").hidden,
  }));
  record(S2, "そのボタンから新規登録すると、回答の画面のその質問（2ページ目）に戻る",
    back.url === `/answers${questionHash}` && back.page2, JSON.stringify(back));
  await guest.close();

  // ── 3. QRで共有（坂担当のQR画像・森下担当の閲覧） ──
  const S3 = "3 QRで共有";
  await A.page.goto(`${BASE}/sharing/new`);
  await A.page.waitForFunction(() => !document.querySelector("#step-select").hidden, null, { timeout: 15000 });
  await A.page.check("input[name=scope][value=all]");
  await A.page.click("#step-next");
  await A.page.check("input[name=mode][value=timed]");
  await A.page.dispatchEvent("input[name=mode][value=timed]", "change");
  await A.page.fill("#expires-at", localInMinutes(60));
  await A.page.click("#step-next");
  await A.page.waitForSelector("#step-confirm:not([hidden])");
  const preview = (await A.page.locator("#preview-output").textContent()) || "";
  record(S3, "共有の再確認に、回答の画面で答えた内容が出る", preview.includes("住み慣れた自宅で暮らしたい") && preview.includes("テスト用の自由記述です"));
  await A.page.check("#confirm-share");
  await A.page.dispatchEvent("#confirm-share", "change");
  await A.page.click("#create-qr");
  await A.page.waitForFunction(() => document.querySelector("#share-url").value.includes("#k="), null, { timeout: 15000 });
  const qrImage = await A.page.locator("#qr-image img, #qr-image canvas, #qr-image svg").count();
  record(S3, "共有リンクのQRコードが画面に出る", qrImage > 0);
  const qrUrl = await A.page.locator("#share-url").inputValue();
  const viewer = await browser.newContext({ timezoneId: TIMEZONE });
  const view = await viewer.newPage();
  await view.goto(qrUrl);
  await view.click("#open");
  await view.waitForFunction(() => document.querySelector("#output").textContent.includes("住み慣れた自宅"), null, { timeout: 15000 }).catch(() => {});
  const viewed = (await view.locator("#output").textContent()) || "";
  record(S3, "別の端末でQRのリンクを開くと、質問と回答が読める",
    viewed.includes("将来、どんな生活を大切にしたいですか？") && viewed.includes("住み慣れた自宅で暮らしたい"));
  await view.screenshot({ path: `${OUT}/3-qr-view.png`, fullPage: true });
  await view.click("#end");
  await viewer.close();

  // ── 4. LINEの受信口（小橋担当） ──
  const S4 = "4 LINEの受信口";
  const empty = { destination: "U" + "2".repeat(32), events: [] };
  record(S4, "署名つきの確認用の要求（イベントなし）に200を返す", (await lineCallback(empty)) === 200);
  record(S4, "署名が違う要求は400で断る", (await lineCallback(empty, "invalid")) === 400);
  record(S4, "署名のない要求は400で断る", (await lineCallback(empty, null)) === 400);

  // ── 5. 回答の画面：ログインが切れたあとの保存、壊れたURL、確認の一覧 ──
  const S5 = "5 回答の画面の例外";
  await openAnswers(A.page, "#%E0%A4%A");
  record(S5, "#以降が壊れたURLでも、1ページ目から入力できる",
    !(await A.page.isDisabled("#acp-fields")) && await A.page.isVisible("#page1"), await statusText(A.page));
  await A.page.click("#checkAnswers");
  await choose(A.page, 3, "旅行や外出");
  record(S5, "回答を変えると、前に出した「あなたの回答」の一覧を隠す", await A.page.isHidden("#resultArea"));
  await A.page.fill("#freeText", "ログインが切れる前に書いた文章");
  // 別のタブでログアウトする（セッションの期限切れと同じ状態）。
  const other = await A.context.newPage();
  await other.goto(`${BASE}/account`);
  await other.waitForFunction(() => !document.querySelector("#logout").disabled && !document.querySelector("#logout").hidden);
  await other.click("#logout");
  await other.waitForFunction(async () => !(await (await fetch("/api/v1/auth/session")).json()).authenticated);
  await other.close();
  await A.page.click("#saveAnswers");
  await A.page.waitForSelector("#unlock-here:not([hidden])", { timeout: 10000 }).catch(() => {});
  const lost = await A.page.evaluate(() => ({
    button: !document.querySelector("#unlock-here").hidden ? document.querySelector("#unlock-here").textContent : "",
    message: document.querySelector("#saveMessage").textContent,
    free: document.querySelector("#freeText").value,
  }));
  record(S5, "ログインが切れて保存できないとき、入力を残して本人確認のやり直しを出す",
    lost.button.includes("もう一度本人確認") && lost.message.includes("まだ保存していません") && lost.free === "ログインが切れる前に書いた文章", JSON.stringify(lost));
  await A.page.click("#unlock-here");
  await A.page.waitForSelector("#unlock-here", { state: "hidden", timeout: 15000 }).catch(() => {});
  record(S5, "この画面で本人確認をやり直せる（入力はそのまま）",
    (await statusText(A.page)).includes("本人確認ができました") && await A.page.inputValue("#freeText") === "ログインが切れる前に書いた文章", await statusText(A.page));
  await A.page.click("#saveAnswers");
  await A.page.waitForFunction(() => document.querySelector("#saveMessage").textContent === "回答を保存しました。", null, { timeout: 10000 }).catch(() => {});
  await openAnswers(A.page);
  record(S5, "やり直したあとに保存でき、開き直しても残っている",
    await A.page.inputValue("#freeText") === "ログインが切れる前に書いた文章", await statusText(A.page));

  // ── 6. 画面を開いた直後のメニュー、端末移行の完了の表示 ──
  const S6 = "6 メニューと端末移行";
  await A.page.addInitScript(() => {
    window.__menuMax = 0;
    const started = performance.now();
    const tick = () => {
      const menu = document.querySelector("#site-menu");
      if (menu && getComputedStyle(menu).position === "fixed") window.__menuMax = Math.max(window.__menuMax, menu.getBoundingClientRect().right);
      if (performance.now() - started < 800) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await A.page.goto(`${BASE}/sharing`);
  await A.page.waitForTimeout(900);
  const menuMax = await A.page.evaluate(() => Math.round(window.__menuMax));
  record(S6, "スマートフォン幅で開いた直後に、メニューが画面を覆って閉じる動きをしない", menuMax <= 0, `メニューの右端の最大 ${menuMax}px`);
  await A.page.goto(`${BASE}/transfer`);
  await A.page.click("#unlock-here");
  await A.page.waitForFunction(() => document.querySelector("#lock-state").textContent.includes("解除されています"), null, { timeout: 15000 });
  await A.page.click("#source-start");
  await A.page.waitForFunction(() => document.querySelector("#source-id").textContent.trim().length > 10);
  const migrationId = (await A.page.locator("#source-id").textContent()).trim();
  // 移行の途中で、通常のログアウト（期限切れと同じ401）が起きても、移行IDを失わず、ログインし直すと続きを追える。
  const logoutTab = await A.context.newPage();
  await logoutTab.goto(`${BASE}/account`);
  await logoutTab.waitForFunction(() => !document.querySelector("#logout").disabled && !document.querySelector("#logout").hidden);
  await logoutTab.click("#logout");
  await logoutTab.waitForFunction(async () => !(await (await fetch("/api/v1/auth/session")).json()).authenticated);
  await logoutTab.close();
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("ログインが終了しました"), null, { timeout: 10000 }).catch(() => {});
  const paused = await A.page.evaluate(() => ({ kept: sessionStorage.getItem("wn-transfer-source"), status: document.querySelector("#status").textContent }));
  record(S6, "移行の途中でログインが切れても、移行IDを残し、ログインし直す案内を出す",
    paused.kept === migrationId && paused.status.includes("ログインし直して"), JSON.stringify(paused).slice(0, 160));
  await A.page.reload();
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("ログインが終了しました"), null, { timeout: 10000 }).catch(() => {});
  record(S6, "ログインが切れたまま開き直しても、移行IDが残っている",
    await A.page.evaluate(() => sessionStorage.getItem("wn-transfer-source")) === migrationId);
  await A.page.click("#login-here");
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("ログインしました"), null, { timeout: 15000 }).catch(() => {});
  record(S6, "ログインし直すと、鍵を解除すれば続きを確認できると案内する", (await statusText(A.page)).includes("進行中の移行の続き"), await statusText(A.page));
  await A.page.click("#unlock-here");
  await A.page.waitForFunction(() => document.querySelector("#source-state").textContent.includes("started"), null, { timeout: 15000 }).catch(() => {});
  record(S6, "鍵を解除すると、進行中の移行の状態をまた確認できる",
    (await A.page.locator("#source-state").textContent()).includes("started"), `${await A.page.locator("#source-state").textContent()} / ${await statusText(A.page)}`);
  const fresh = await browser.newContext({ viewport: { width: 420, height: 1400 }, timezoneId: TIMEZONE });
  const N = await fresh.newPage();
  const freshCdp = await fresh.newCDPSession(N);
  await freshCdp.send("WebAuthn.enable");
  await freshCdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", ctap2Version: "ctap2_1", transport: "internal", hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: true,
  } });
  const waitStatus = (page, text) => page.waitForFunction(needle => document.querySelector("#status").textContent.includes(needle), text, { timeout: 20000 });
  await N.goto(`${BASE}/transfer`);
  await N.fill("#target-id", migrationId);
  await N.click("#target-bind");
  await waitStatus(N, "一致しています");
  const code = await N.locator("#target-code").textContent();
  await A.page.waitForFunction(expected => document.querySelector("#source-code").textContent === expected, code, { timeout: 10000 });
  await A.page.fill("#typed-code", code);
  await A.page.click("#source-approve");
  await waitStatus(A.page, "鍵束を送りました");
  await N.click("#target-finish");
  await waitStatus(N, "新しいパスキーを登録しました");
  await N.click("#login-here");
  await waitStatus(N, "ログインしました");
  await N.click("#target-confirm");
  await waitStatus(N, "復号できました");
  await N.click("#target-finalize");
  await waitStatus(N, "移行を完了しました");
  await A.page.waitForFunction(() => document.querySelector("#status").textContent.includes("ログインが終了しました")
    || document.querySelector("#status").textContent.includes("移行が完了しました"), null, { timeout: 10000 }).catch(() => {});
  record(S6, "新しい端末で移行を完了すると、古い端末の画面にも終了が出る",
    /ログインが終了しました|移行が完了しました/.test(await statusText(A.page)), await statusText(A.page));
  await N.goto(`${BASE}/answers`);
  await N.waitForFunction(() => !document.querySelector("#acp-fields").disabled, null, { timeout: 15000 });
  record(S6, "新しい端末で、移行前の回答を読める", await N.inputValue("#freeText") === "ログインが切れる前に書いた文章");
  await fresh.close();

  record("全体", "ページのスクリプトエラーがない", A.errors.length === 0, A.errors.join(" / "));
  fs.writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  console.log(`結果と画面の記録: ${OUT}`);
  const failed = results.filter(item => !item.ok);
  console.log(`\n${results.length - failed.length}/${results.length} 件 OK`);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch(error => { console.error("SCRIPT ERROR", error); process.exit(2); });
