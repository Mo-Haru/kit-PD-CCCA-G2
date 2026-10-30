import {
  api, createFamilyShare, getUnlockState, loadAnswerDocument, unlock, updateFamilyShare,
} from "./client.mjs";
import { entriesFromPayload, fillAnswerList, plaintextForSelection, presentAnswer, shareFingerprint } from "./crypto.mjs";
import { confirmationCoversShare, confirmationStillMatches, shareSteps, toIso } from "./family-share-flow.mjs";

const scopeLabel = { all: "全体", chapters: "章", questions: "質問" };

// 家族への共有を始める画面。ロック解除 → 共有内容の選択 → 家族と期限 → 再確認 → 共有確定、の順に進む。
// 更新（?update=共有ID）では、相手と期限を変えないので「家族と期限」の手順を省く。
export function bindFamilyShare(root, client = {
  api, createFamilyShare, getUnlockState, loadAnswerDocument, unlock, updateFamilyShare,
}, options = {}) {
  if (!root.querySelector("#family-share-page")) return Promise.resolve();
  const node = id => root.querySelector(`#${id}`);
  const updateId = options.updateId || null;
  const steps = shareSteps(Boolean(updateId));
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
  let unavailable = "";
  let previewKey = "";
  let confirmed = null;
  let updating = null;
  let connections = [];

  function show(text, error = false) {
    node("status").textContent = text;
    node("status").classList.toggle("is-error", error);
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
  function hasSelection() {
    const chosen = selection();
    if (chosen.scope === null) return false;
    return chosen.scope === "all" ? catalog.questions.length > 0 : chosen.ids.length > 0;
  }
  function selectionProblem() {
    const chosen = selection();
    if (chosen.scope === null) return "共有範囲（全体・章・質問）を選んでください。";
    if (chosen.scope === "all") return catalog.questions.length > 0 ? "" : "共有できる質問がありません。";
    return chosen.ids.length > 0 ? "" : chosen.scope === "chapters" ? "共有する章を1つ以上選んでください。" : "共有する質問を1つ以上選んでください。";
  }
  function expiryProblem() {
    const input = node("expires-at");
    if (input.validity?.badInput) return "期限の日付と時刻を最後まで入力してください。";
    if (input.value && !(new Date(input.value).getTime() > Date.now())) return "期限は現在より後にしてください。";
    return "";
  }
  // 今の手順で「次へ」を押せない理由。押せるときは空文字。
  function blocker() {
    if (unavailable) return unavailable;
    const step = steps[current].id;
    if (step === "unlock") return unlocked() ? "" : "回答の暗号化を解除してください。";
    if (step === "select") return selectionProblem();
    if (step === "target") return node("connection").value ? expiryProblem() : "共有できる家族がいません。";
    return "";
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
    for (const id of ["unlock", "select", "target", "confirm", "done"]) node(`step-${id}`).hidden = id !== step;
    renderSteps();
    const loggedIn = Boolean(session?.authenticated);
    node("login-here").hidden = !session || loggedIn;
    node("unlock-here").hidden = Boolean(session) && !loggedIn;
    node("unlock-here").disabled = busy || unlocking || !loggedIn || accountChanged || unlocked();
    node("unlock-here").dataset.unlockState = unlocked() ? "unlocked" : "locked";
    node("unlock-here").textContent = unlocked() ? "解除済み" : unlocking ? "解除中…" : "回答の暗号化を解除";
    node("lock-state").textContent = accountChanged ? "アカウントが変わりました。ページを開き直してください。"
      : !session ? "状態を確認しています。"
        : !loggedIn ? "ログインすると、家族に共有できます。"
          : unlocked() ? `${session.display_name} さん：解除済み。` : `${session.display_name} さん：回答は暗号化されています。`;
    node("step-back").hidden = finished || current === 0;
    node("step-back").disabled = busy;
    node("step-next").hidden = finished || step === "confirm";
    node("step-next").disabled = busy || unlocking || accountChanged || blocker() !== "";
    node("share").hidden = finished || step !== "confirm";
    node("confirm-share").disabled = busy || !previewKey;
    node("share").disabled = busy || !unlocked() || !previewKey || !node("confirm-share").checked;
    node("no-family").hidden = !session?.authenticated || connections.length > 0 || updateId !== null;
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
        view.node.textContent = answer.stateLabel === "回答" ? `回答：${answer.valueText}` : answer.stateLabel;
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
    scopeVisibility();
  }
  function scopeVisibility() {
    const scope = root.querySelector("input[name=scope]:checked")?.value || null;
    node("chapters").hidden = scope !== "chapters";
    node("questions").hidden = scope !== "questions";
    node("all-answers").hidden = scope !== "all";
  }

  function clearConfirmation() {
    previewKey = "";
    confirmed = null;
    node("confirm-share").checked = false;
    node("preview-output").replaceChildren();
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
    return plaintextForSelection(opened.document, catalog, selection());
  }
  async function currentContext() {
    let target;
    if (updating) {
      target = { id: updating.connection_id, userId: updating.recipient_user_id, name: updating.recipient_display_name || "家族" };
    } else {
      if (!node("connection").value) throw new Error("共有する家族を選んでください。");
      const chosen = JSON.parse(node("connection").value);
      const option = node("connection").selectedOptions?.[0];
      target = { id: chosen.id, userId: chosen.userId, name: option ? option.textContent : chosen.userId };
    }
    const keys = await client.api(`/api/v1/encryption-keys/${target.userId}`);
    const active = (keys.keys || []).find(item => item.status === "active");
    if (!active) throw new Error("相手の受信用公開鍵がありません。");
    return {
      connectionId: target.id,
      recipientUserId: target.userId,
      recipientKeyId: active.id,
      expiresAt: updating ? (updating.expires_at ?? null) : toIso(node("expires-at").value),
      recipientName: target.name,
    };
  }
  function showFacts(context, chosen, payload) {
    const facts = [
      `相手：${context.recipientName}`,
      `鍵：${context.recipientKeyId}`,
      `期限：${context.expiresAt ? new Date(context.expiresAt).toLocaleString() : "なし"}${updating ? "（更新では変わりません）" : ""}`,
      `範囲：${scopeLabel[chosen.scope]}`,
    ].map(text => {
      const row = root.createElement("p");
      row.textContent = text;
      return row;
    });
    const answers = root.createElement("div");
    fillAnswerList(answers, entriesFromPayload(payload, catalog));
    node("preview-output").replaceChildren(...facts, answers);
  }
  // 再確認の画面を作る。ここで読んだ回答・相手・鍵・期限だけが、確定で共有される。
  async function buildConfirmation() {
    clearConfirmation();
    await readSession();
    const chosen = selection();
    const payload = await loadPayload();
    const context = await currentContext();
    showFacts(context, chosen, payload);
    previewKey = shareFingerprint(chosen, payload, context);
    confirmed = { chosen, payload, context };
    goto(steps.length - 1);
    show("共有する内容を確認してください。問題なければチェックして、「共有を確定」を押します。");
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
    if (!confirmed || !node("confirm-share").checked) throw new Error("共有する内容を確認してからチェックしてください。");
    const chosen = selection();
    const payload = await loadPayload();
    const context = await currentContext();
    if (!confirmationStillMatches(previewKey, chosen, payload, context)) {
      clearConfirmation();
      goto(updateId ? 1 : 2);
      const error = new Error("確認内容が変わったので、もう一度確認してください。相手、期限、鍵、項目を確認し直してください。");
      error.code = "confirmation_stale";
      throw error;
    }
    if (updating) {
      if (!confirmationCoversShare(context, updating)) {
        clearConfirmation();
        throw new Error("確認した相手と接続がこの共有と違います。もう一度確認してください。");
      }
      await client.updateFamilyShare({
        shareId: updating.id,
        expectedVersion: updating.current_version,
        recipientUserId: updating.recipient_user_id,
        recipientKeyId: context.recipientKeyId,
        selection: chosen,
        payload,
      });
    } else {
      await client.createFamilyShare({
        connectionId: context.connectionId,
        recipientUserId: context.recipientUserId,
        recipientKeyId: context.recipientKeyId,
        selection: chosen,
        payload,
        expiresAt: context.expiresAt,
      });
    }
    const name = context.recipientName;
    clearConfirmation();
    finished = true;
    node("done-text").textContent = updating
      ? `${name} さんへの共有を、確認した内容に更新しました。回答を後から保存しても、この共有は自動では変わりません。`
      : `${name} さんに、確認した相手・期限・回答だけを共有しました。回答を後から保存しても、この共有は自動では変わりません。`;
    show(updating ? "共有を更新しました。" : "共有しました。");
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
  node("expires-unlimited").addEventListener("click", () => {
    node("expires-at").value = "";
    clearConfirmation();
    show("期限なしで共有します。");
    controls();
  });
  node("share").addEventListener("click", () => run(submit));
  node("confirm-share").addEventListener("change", controls);
  for (const input of root.querySelectorAll("input[name=scope]")) {
    input.addEventListener("change", () => { scopeVisibility(); clearConfirmation(); controls(); });
  }
  for (const id of ["questions", "chapters", "connection", "expires-at"]) {
    node(id).addEventListener("change", () => { clearConfirmation(); controls(); });
  }
  node("expires-at").addEventListener("input", () => { clearConfirmation(); controls(); });
  node("expires-at").addEventListener("keyup", controls);
  node("expires-at").addEventListener("blur", controls);

  return run(async () => {
    await readSession();
    catalog = await client.api("/api/v1/answers/questions");
    renderCatalog();
    if (!session.authenticated) return;
    encrypted = await client.api("/api/v1/answers/current");
    const connectionBody = await client.api("/api/v1/family/connections");
    connections = connectionBody.connections.filter(connection => connection.status === "active");
    for (const connection of connections) {
      const option = root.createElement("option");
      option.value = JSON.stringify({ id: connection.id, userId: connection.other_user_id });
      option.textContent = connection.other_display_name || connection.other_user_id;
      node("connection").append(option);
    }
    if (updateId) {
      const own = await client.api("/api/v1/family/shares");
      updating = own.shares.find(share => share.id === updateId) || null;
      if (!updating || updating.status === "stopped") {
        updating = null;
        unavailable = "更新する共有が見つかりません。停止・期限切れなどの可能性があります。家族画面から選び直してください。";
        throw new Error(unavailable);
      }
      node("share-title").textContent = `${updating.recipient_display_name || "家族"} さんへの共有を更新する`;
    }
    // 開いたらすぐ、パスキーの確認（解除）を始める。取り消したときは、ボタンで続ける。
    if (!unlocked()) await unlockAnswers();
  });
}

if (typeof document !== "undefined" && document.querySelector("#family-share-page")) {
  bindFamilyShare(document, undefined, { updateId: new URLSearchParams(location.search).get("update") });
}
