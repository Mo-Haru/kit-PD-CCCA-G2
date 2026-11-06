import { notify } from "./site-alert.mjs";
import { api, createQrShare, getUnlockState, loadAnswerDocument, unlock } from "./client.mjs";
import { entriesFromPayload, fillAnswerList, plaintextForSelection, presentAnswer } from "./crypto.mjs";
import { renderQrCanvas } from "./qrShareView.mjs";
import { MAX_DAYS, expiryBounds, longExpiryDefault, modeLabel, qrConfirmationKey, shareSteps } from "./sharing-flow.mjs";

const scopeLabel = { all: "全体", chapters: "章", questions: "質問" };

// 共有リンク（QR）を作る画面。ロック解除 → 共有内容の選択 → リンクの種類と期限 → 再確認 → 共有確定、の順に進む。
// 家族に共有する画面（family-share.mjs）と同じ手順・同じ操作にそろえている。
export function bindSharingNew(root, client = {
  api, createQrShare, getUnlockState, loadAnswerDocument, unlock, renderQr: renderQrCanvas,
}) {
  if (!root.querySelector("#sharing-new-page")) return Promise.resolve();
  const node = id => root.querySelector(`#${id}`);
  const steps = shareSteps();
  const typeIndex = steps.findIndex(step => step.id === "type");
  let current = 0;
  let catalog = { categories: [], questions: [] };
  let session = null;
  let encrypted = null;
  let answerDocument = null;
  let answersReady = false;
  let unlocking = false;
  let busy = false;
  let finished = false;
  let accountChanged = false;
  let previewKey = "";
  let confirmed = null;
  let shownMode = null;

  function show(text, error = false) {
    notify(root, node("status"), text, error);
  }
  function unlocked() {
    return answersReady && !accountChanged && Boolean(session?.authenticated) && client.getUnlockState().unlocked;
  }
  function checked(group) {
    return [...node(group).querySelectorAll("input:checked")].map(input => input.value);
  }
  function selection() {
    // 範囲は、最初は未選択。
    const scope = root.querySelector("input[name=scope]:checked")?.value || null;
    if (scope === null || scope === "all") return { scope };
    return { scope, ids: checked(scope === "chapters" ? "chapters" : "questions") };
  }
  function selectionProblem() {
    const chosen = selection();
    if (chosen.scope === null) return "共有範囲（全体・章・質問）を選んでください。";
    if (chosen.scope === "all") return catalog.questions.length > 0 ? "" : "共有できる質問がありません。";
    return chosen.ids.length > 0 ? "" : chosen.scope === "chapters" ? "共有する章を1つ以上選んでください。" : "共有する質問を1つ以上選んでください。";
  }
  function mode() {
    return root.querySelector("input[name=mode]:checked").value;
  }
  // 種類と期限。期限が足りない・範囲外のときは、理由を持つ例外を投げる。
  function settings() {
    const kind = mode();
    let expiresAt = null;
    if (kind in MAX_DAYS) {
      const input = node("expires-at");
      if (input.validity?.badInput) throw new Error("有効期限の日付と時刻を最後まで入力してください。");
      if (!input.value) throw new Error("有効期限を選んでください。");
      const date = new Date(input.value);
      if (!Number.isFinite(date.getTime()) || date <= new Date() || date > new Date(Date.now() + MAX_DAYS[kind] * 86400000)) {
        throw new Error(kind === "long" ? "有効期限を現在より後、3年以内に指定してください。" : "有効期限を現在より後、31日以内に指定してください。");
      }
      expiresAt = date.toISOString();
    }
    return { selection: selection(), mode: kind, expiresAt };
  }
  // 今の手順で「次へ」を押せない理由。押せるときは空文字。
  function blocker() {
    const step = steps[current].id;
    if (step === "unlock") return unlocked() ? "" : "回答の暗号化を解除してください。";
    if (step === "select") return selectionProblem();
    if (step === "type") {
      try { settings(); } catch (error) { return error.message; }
    }
    return "";
  }

  function visibility() {
    const scope = root.querySelector("input[name=scope]:checked")?.value || null;
    node("chapters").hidden = scope !== "chapters";
    node("questions").hidden = scope !== "questions";
    node("all-answers").hidden = scope !== "all";
    const kind = mode();
    const expiring = kind in MAX_DAYS;
    node("expiry-field").hidden = !expiring;
    node("expires-at").disabled = !expiring;
    node("long-warning").hidden = kind !== "long";
    if (expiring) {
      const { min, max } = expiryBounds(kind);
      node("expires-at").min = min;
      node("expires-at").max = max;
    }
    if (kind !== shownMode) {
      // 長期は上限の3年後を既定にする。期限付きへ戻したら選び直してもらう。
      node("expires-at").value = kind === "long" ? longExpiryDefault() : "";
      shownMode = kind;
    }
  }

  function renderSteps() {
    // 終わった手順は、押して戻れる。先へは「次へ」で進む。
    node("share-steps").replaceChildren(...steps.map((step, index) => {
      const item = root.createElement("li");
      const chip = root.createElement("button");
      chip.type = "button";
      chip.textContent = `${index + 1}. ${step.label}`;
      chip.disabled = finished || busy || unlocking || index >= current;
      chip.addEventListener("click", () => jump(index));
      item.dataset.stepState = finished || index < current ? "done" : index === current ? "current" : "todo";
      if (!finished && index === current) item.setAttribute("aria-current", "step");
      item.append(chip);
      return item;
    }));
  }
  function controls() {
    const step = finished ? "done" : steps[current].id;
    for (const id of ["unlock", "select", "type", "confirm", "done"]) node(`step-${id}`).hidden = id !== step;
    renderSteps();
    const loggedIn = Boolean(session?.authenticated);
    node("login-here").hidden = !session || loggedIn;
    node("unlock-here").hidden = Boolean(session) && !loggedIn;
    node("unlock-here").disabled = busy || unlocking || !loggedIn || accountChanged || unlocked();
    node("unlock-here").dataset.unlockState = unlocked() ? "unlocked" : "locked";
    node("unlock-here").textContent = unlocked() ? "解除済み" : unlocking ? "解除中…" : "回答の暗号化を解除";
    node("lock-state").textContent = accountChanged ? "アカウントが変わりました。ページを開き直してください。"
      : !session ? "状態を確認しています。"
        : !loggedIn ? "ログインすると、共有リンクを作れます。"
          : unlocked() ? `${session.display_name} さん：解除済み。` : `${session.display_name} さん：回答は暗号化されています。`;
    node("step-back").hidden = finished || current === 0;
    node("step-back").disabled = busy;
    node("step-next").hidden = finished || step === "confirm";
    node("step-next").disabled = busy || unlocking || accountChanged || blocker() !== "";
    node("create-qr").hidden = finished || step !== "confirm";
    node("confirm-share").disabled = busy || !previewKey;
    node("create-qr").disabled = busy || !unlocked() || !previewKey || !node("confirm-share").checked;
    renderAnswers();
  }
  function goto(index) {
    current = index;
    controls();
  }
  function jump(index) {
    if (finished || busy || unlocking || index < 0 || index >= current) return;
    clearConfirmation();
    show("");
    goto(index);
  }

  const answerViews = [];
  function appendAnswer(parent, question) {
    const row = root.createElement("span");
    row.className = "sharing-answer-row";
    const title = root.createElement("span");
    title.className = "sharing-question-title";
    title.textContent = question.text || question.id;
    const value = root.createElement("span");
    value.className = "sharing-answer-value";
    answerViews.push({ id: question.id, node: value });
    row.append(title, value);
    parent.append(row);
  }
  function renderAnswers() {
    const ready = unlocked();
    for (const view of answerViews) {
      view.node.dataset.answerState = ready ? "decrypted" : "encrypted";
      if (ready) {
        const answer = presentAnswer(answerDocument?.items?.[view.id]);
        view.node.textContent = answer.stateLabel === "回答" ? `回答：${answer.valueText || "（空欄）"}` : answer.stateLabel;
      } else {
        view.node.textContent = !session?.authenticated ? "ログインして回答を確認"
          : encrypted?.document === null ? "保存済みの回答はありません"
            : encrypted?.document ? `暗号化中：${encrypted.document.ciphertext.slice(0, 64)}…` : "回答データを確認しています…";
      }
    }
  }
  function choice(value, label) {
    const row = root.createElement("label");
    const input = root.createElement("input");
    input.type = "checkbox";
    input.value = value;
    row.append(input, root.createTextNode(` ${label}`));
    return row;
  }
  function renderCatalog() {
    const seen = new Set();
    for (const category of catalog.categories || []) {
      seen.add(category.id);
      const group = root.createElement("div");
      group.className = "sharing-chapter";
      group.append(choice(category.id, category.name || category.id));
      for (const question of catalog.questions.filter(question => question.category_id === category.id)) appendAnswer(group, question);
      node("chapters").append(group);
    }
    for (const question of catalog.questions || []) {
      if (!seen.has(question.category_id)) {
        seen.add(question.category_id);
        node("chapters").append(choice(question.category_id, question.category_name || question.category_id));
      }
      const label = choice(question.id, "");
      appendAnswer(label, question);
      node("questions").append(label);
      appendAnswer(node("all-answers"), question);
    }
  }

  function clearConfirmation() {
    previewKey = "";
    confirmed = null;
    node("confirm-share").checked = false;
    node("preview-output").replaceChildren();
  }
  function clearLink() {
    node("qr-image").replaceChildren();
    node("share-url").value = "";
    node("open-link").removeAttribute("href");
    node("link-facts").textContent = "";
  }
  function resetAccess() {
    answersReady = false;
    answerDocument = null;
    clearConfirmation();
  }
  function handleError(error) {
    if (error.code === "reauth_required" || error.status === 401) {
      resetAccess();
      current = 0;
    }
    const message = ["NotAllowedError", "AbortError"].includes(error.name)
      ? "暗号化の解除を完了できませんでした。もう一度お試しください。"
      : error.code === "reauth_required" ? "本人確認の期限が切れました。回答の暗号化をもう一度解除してください。"
        : error.message || "操作できませんでした。";
    show(message, true);
    controls();
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    controls();
    try { await action(); }
    catch (error) { handleError(error); }
    finally { busy = false; controls(); }
  }

  async function readSession() {
    if (accountChanged) throw new Error("アカウントが変わりました。ページを開き直してください。");
    const latest = await client.api("/api/v1/auth/session");
    if (session?.user_id && latest.user_id !== session.user_id) {
      accountChanged = true;
      resetAccess();
      clearLink();
      controls();
      throw new Error("アカウントが変わりました。ページを開き直してください。");
    }
    session = latest;
    return latest;
  }
  async function loadPayload() {
    if (!unlocked()) throw new Error("先に回答の暗号化を解除してください。");
    const opened = await client.loadAnswerDocument();
    answerDocument = opened.document;
    renderAnswers();
    if (opened.revision === 0) throw new Error("保存済みの回答がありません。先に回答画面でDBへ保存してください。");
    return plaintextForSelection(opened.document, catalog, selection());
  }
  function showFacts(chosen, payload) {
    const facts = [
      `リンクの種類：${modeLabel(chosen.mode)}`,
      `期限：${chosen.expiresAt ? new Date(chosen.expiresAt).toLocaleString() : "なし（最初の閲覧のみ）"}`,
      `範囲：${scopeLabel[chosen.selection.scope]}`,
    ].map(text => {
      const row = root.createElement("p");
      row.className = "answer-fact";
      row.textContent = text;
      return row;
    });
    if (chosen.mode === "long") {
      const caution = root.createElement("p");
      caution.className = "is-error";
      caution.textContent = "長期リンクです。期限までは、リンクを手に入れた人なら誰でも何回でも閲覧できます。";
      facts.push(caution);
    }
    const answers = root.createElement("div");
    fillAnswerList(answers, entriesFromPayload(payload, catalog));
    node("preview-output").replaceChildren(...facts, answers);
  }
  // 再確認の画面を作る。ここで読んだ回答・種類・期限だけが、確定で共有される。
  async function buildConfirmation() {
    clearConfirmation();
    await readSession();
    const chosen = settings();
    const payload = await loadPayload();
    showFacts(chosen, payload);
    previewKey = qrConfirmationKey(chosen.selection, chosen.mode, chosen.expiresAt, session.user_id);
    confirmed = { ...chosen, payload };
    goto(steps.length - 1);
    show("共有する内容と期限を確認してください。問題なければチェックして、「共有リンクを作る」を押します。");
  }

  async function unlockAnswers() {
    if (unlocked()) return;
    unlocking = true;
    controls();
    show("回答の暗号化を解除しています…");
    try {
      await client.unlock();
      await readSession();
      const loaded = await client.loadAnswerDocument();
      answerDocument = loaded.document;
      answersReady = true;
      // 解除したら、共有範囲の選択へ進む。
      if (steps[current].id === "unlock") current += 1;
      show("回答の暗号化を解除しました。共有範囲を選択してください。");
    } finally { unlocking = false; controls(); }
  }
  async function submit() {
    await readSession();
    if (!unlocked()) throw new Error("先に回答の暗号化を解除してください。");
    const chosen = settings();
    if (!confirmed || !node("confirm-share").checked
      || previewKey !== qrConfirmationKey(chosen.selection, chosen.mode, chosen.expiresAt, session.user_id)) {
      clearConfirmation();
      goto(typeIndex);
      throw new Error("共有する内容をもう一度確認してください。");
    }
    // 確認した内容だけを共有する。確認のあとに回答を保存し直しても、ここでは読み直さない。
    const created = await client.createQrShare({ ...chosen, payload: structuredClone(confirmed.payload) });
    // QRにするのは created.url だけ。画像は端末の中だけで描く。
    node("qr-image").replaceChildren(client.renderQr(created.url, { doc: root }));
    node("share-url").value = created.url;
    node("open-link").href = created.url;
    node("link-facts").textContent = `作成日時 ${created.share.created_at} / ${modeLabel(created.share.mode)}`;
    clearConfirmation();
    finished = true;
    show("共有リンクとQRコードを作成しました。開くだけでは閲覧を開始せず、「内容を開く」で開始します。");
  }

  node("unlock-here").addEventListener("click", () => run(unlockAnswers));
  node("step-next").addEventListener("click", () => run(async () => {
    const reason = blocker();
    if (reason) throw new Error(reason);
    show("");
    if (steps[current + 1].id === "confirm") await buildConfirmation();
    else goto(current + 1);
  }));
  node("step-back").addEventListener("click", () => jump(current - 1));
  node("create-qr").addEventListener("click", () => run(submit));
  node("confirm-share").addEventListener("change", controls);
  node("copy-link").addEventListener("click", () => run(async () => {
    await navigator.clipboard.writeText(node("share-url").value);
    show("共有リンクをコピーしました。");
  }));
  for (const input of [...root.querySelectorAll("input[name=scope]"), ...root.querySelectorAll("input[name=mode]")]) {
    // 範囲・種類を変えたら、期限の既定値を入れてから、押せるかを判定する。
    input.addEventListener("change", () => { visibility(); clearConfirmation(); controls(); });
  }
  for (const id of ["questions", "chapters"]) {
    node(id).addEventListener("change", () => { clearConfirmation(); controls(); });
  }
  node("expires-at").addEventListener("input", () => { clearConfirmation(); controls(); });
  // 入力の途中は value が空のままで input が起きないので、理由の表示をここで更新する。
  node("expires-at").addEventListener("keyup", controls);
  node("expires-at").addEventListener("blur", controls);
  // 開いたままの画面でも、選べる範囲を今の時刻に合わせる。
  node("expires-at").addEventListener("focus", visibility);

  visibility();
  return run(async () => {
    await readSession();
    catalog = await client.api("/api/v1/answers/questions");
    renderCatalog();
    if (!session.authenticated) return;
    encrypted = await client.api("/api/v1/answers/current");
    // 開いたらすぐ、パスキーの確認（解除）を始める。取り消したときは、ボタンで続ける。
    if (!unlocked()) await unlockAnswers();
  });
}

if (typeof document !== "undefined" && document.querySelector("#sharing-new-page")) bindSharingNew(document);
