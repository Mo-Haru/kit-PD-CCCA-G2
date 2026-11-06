import * as client from "./client.mjs";
import { createDemoAnswerStore } from "./answer-demo-store.mjs";
import { notify } from "./site-alert.mjs";

// 人生会議（ACP）の質問に答える画面（今牧担当の画面を統合）。
// 回答は、ブラウザ内で暗号化した回答文書（/api/v1/answers）にだけ保存する。localStorage には置かない。
// 選択肢の回答は、選んだ選択肢の文（例：「家族」）を保存する。

// 1つの質問で選べるのは1つだけ。同じ選択肢をもう一度押すと、選択を外せる。
// previous は保存済みの回答。同じ回答のままなら回答日（answered_at）を引き継ぎ、変わったら今日の日付にする。
export function readAnswers(root, previous = {}, today = localToday()) {
  const items = {};
  for (const card of root.querySelectorAll("[data-acp-question]")) {
    const id = card.dataset.questionId;
    const checked = card.querySelector(".answer-checkbox:checked");
    const text = card.querySelector("textarea");
    const value = checked ? checked.dataset.answerLabel : text ? text.value.trim() : "";
    if (!value) continue;
    const before = previous[id];
    const date = before?.state === "answered" && before.value === value && before.answered_at ? before.answered_at : today;
    items[id] = { state: "answered", value, answered_at: date };
  }
  return items;
}

export function localToday(now = new Date()) {
  const pad = number => String(number).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// 保存済みの回答を入力欄に戻す。選択肢にない値は入力に戻さず、件数を返す（保存時は上書きしない）。
export function restoreAnswers(root, items) {
  const unknown = [];
  for (const card of root.querySelectorAll("[data-acp-question]")) {
    const id = card.dataset.questionId;
    const item = items?.[id];
    const value = item?.state === "answered" && typeof item.value === "string" ? item.value : "";
    const text = card.querySelector("textarea");
    if (text) { text.value = value; continue; }
    let matched = false;
    for (const box of card.querySelectorAll(".answer-checkbox")) {
      box.checked = Boolean(value) && box.dataset.answerLabel === value;
      matched ||= box.checked;
    }
    if (item && !matched) unknown.push(id);
  }
  return unknown;
}

export function summarize(root, items) {
  const groups = [];
  for (const card of root.querySelectorAll("[data-acp-question]")) {
    const title = card.dataset.categoryTitle;
    let group = groups.at(-1);
    if (!group || group.title !== title) groups.push(group = { title, rows: [] });
    const number = card.querySelector("h3")?.textContent || "";
    const value = items[card.dataset.questionId]?.value;
    group.rows.push({ label: number ? `${number}：${card.dataset.questionTitle}` : card.dataset.questionTitle, value: value || "未回答" });
  }
  return groups;
}

export function bindAnswersPage(root, api = client) {
  const node = id => root.getElementById(id);
  const fields = node("acp-fields");
  const ids = [...root.querySelectorAll("[data-acp-question]")].map(card => card.dataset.questionId);
  const store = createDemoAnswerStore(api, ids);
  const pages = [...root.querySelectorAll("[data-acp-page]")];
  let page = 0;
  let busy = false;
  let ready = false;
  let dirty = false;
  // 保存済みでも、この画面の選択肢にない回答。保存のときは、元の値を残す。
  let kept = {};
  let previous = {};

  function show(text, error = false) { notify(root, node("status"), text, error); }
  function saved(text) { node("saveMessage").textContent = text; }
  function sync() {
    fields.disabled = !ready || busy;
    node("unlock-here").disabled = busy;
    node("saveAnswers").disabled = !ready || busy || !dirty;
  }
  function showPage(index, { scroll = false } = {}) {
    page = index;
    pages.forEach((element, number) => { element.hidden = number !== index; });
    node("prevPage").hidden = index === 0;
    node("nextPage").hidden = index >= pages.length - 1;
    // 本文は共通レイアウトの .site-content の中でスクロールするので、window ではなく新しいページの先頭へ移る。
    if (scroll) pages[index]?.scrollIntoView?.({ block: "start" });
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    sync();
    try { await action(); }
    catch (error) {
      const message = error.code === "revision_conflict"
        ? "別の画面で回答が更新されています。入力は残しています。内容を控えてから、ページを開き直して最新の回答を確認してください。"
        : ["NotAllowedError", "AbortError"].includes(error.name)
          ? "暗号化の解除を完了できませんでした。「回答の暗号化を解除」でもう一度お試しください。"
          : error.message || "処理できませんでした。入力は保存していません。";
      show(message, true);
    } finally { busy = false; sync(); }
  }
  async function load() {
    ready = false;
    const catalog = await api.api("/api/v1/answers/questions");
    if (!ids.every(id => catalog.questions.some(question => question.id === id))) {
      throw new Error("回答画面の質問がDBに登録されていません。管理者が seed-questions コマンドを実行してください。");
    }
    try {
      const loaded = await store.load();
      const unknown = restoreAnswers(root, loaded.items);
      kept = Object.fromEntries(unknown.map(id => [id, loaded.items[id]]));
      previous = loaded.items;
      ready = true;
      dirty = false;
      node("unlock-here").hidden = true;
      show(loaded.revision === 0 ? "保存済みの回答はありません。答えたい質問から選んでください。"
        : unknown.length ? `保存済みの回答を読み込みました。今の選択肢にない回答が${unknown.length}件あり、変えずに残しています。`
          : "保存済みの回答を読み込みました。");
    } catch (error) { store.invalidate(); throw error; }
  }
  async function unlockAndLoad() {
    show("回答の暗号化を解除しています…");
    await api.unlock();
    await load();
  }

  for (const box of root.querySelectorAll(".answer-checkbox")) {
    box.addEventListener("change", () => {
      if (box.checked) {
        for (const other of box.closest("[data-acp-question]").querySelectorAll(".answer-checkbox")) {
          if (other !== box) other.checked = false;
        }
      }
      delete kept[box.name];
      dirty = true;
      saved("");
      sync();
    });
  }
  node("freeText")?.addEventListener("input", () => { dirty = true; saved(""); sync(); });
  for (const term of root.querySelectorAll("[data-acp-term]")) {
    term.addEventListener("click", () => {
      const note = node(term.getAttribute("aria-controls"));
      note.hidden = !note.hidden;
      term.setAttribute("aria-expanded", String(!note.hidden));
    });
  }
  node("openExplanation").addEventListener("click", () => {
    node("acpExplanation").hidden = false;
    node("openExplanation").setAttribute("aria-expanded", "true");
    node("acpExplanation").scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  });
  node("closeExplanation").addEventListener("click", () => {
    node("acpExplanation").hidden = true;
    node("openExplanation").setAttribute("aria-expanded", "false");
  });
  node("nextPage").addEventListener("click", () => showPage(Math.min(page + 1, pages.length - 1), { scroll: true }));
  node("prevPage").addEventListener("click", () => showPage(Math.max(page - 1, 0), { scroll: true }));
  node("unlock-here").addEventListener("click", () => run(unlockAndLoad));
  node("saveAnswers").addEventListener("click", () => run(async () => {
    saved("回答を暗号化して保存しています…");
    const items = { ...kept, ...readAnswers(root, previous) };
    const result = await store.save(items);
    previous = items;
    dirty = false;
    saved("回答を保存しました。");
    show(`回答を暗号化して保存しました（版 ${result.revision}）。家族や共有リンクへの共有は、自動では変わりません。`);
  }));
  node("checkAnswers").addEventListener("click", () => {
    const list = node("answerMessage");
    list.replaceChildren();
    for (const group of summarize(root, { ...kept, ...readAnswers(root, previous) })) {
      const heading = root.createElement("h3");
      heading.textContent = group.title;
      const rows = root.createElement("dl");
      for (const row of group.rows) {
        const term = root.createElement("dt");
        term.textContent = row.label;
        const value = root.createElement("dd");
        value.textContent = row.value;
        rows.append(term, value);
      }
      list.append(heading, rows);
    }
    node("resultArea").hidden = false;
    node("resultArea").focus?.();
  });
  node("resetAnswers").addEventListener("click", () => {
    if (!root.defaultView?.confirm?.("入力した回答をすべてリセットしますか？\n「途中保存する」を押すまで、保存済みの回答は変わりません。")) return;
    for (const box of root.querySelectorAll(".answer-checkbox")) box.checked = false;
    if (node("freeText")) node("freeText").value = "";
    kept = {};
    dirty = true;
    node("resultArea").hidden = true;
    saved("入力をリセットしました。「途中保存する」を押すと、保存済みの回答も消えます。");
    sync();
  });

  // わたしノートの「書き直す」から来たときは、その質問のあるページを開く。
  const target = root.defaultView?.location?.hash ? root.getElementById(decodeURIComponent(root.defaultView.location.hash.slice(1))) : null;
  const targetPage = target ? pages.findIndex(element => element.contains(target)) : -1;
  showPage(Math.max(targetPage, 0));
  target?.scrollIntoView?.({ block: "start" });
  sync();
  return run(async () => {
    const session = await api.api("/api/v1/auth/session");
    node("login-here").hidden = session.authenticated;
    node("unlock-here").hidden = !session.authenticated;
    if (!session.authenticated) {
      show("ログインすると、回答を暗号化して保存できます。");
      return;
    }
    // 開いたらすぐ、パスキーの確認（解除）を始める。取り消したときは、ボタンで続ける。
    if (api.getUnlockState().unlocked) await load();
    else await unlockAndLoad();
  });
}

if (typeof document !== "undefined" && document.querySelector(".acp-answers")) bindAnswersPage(document);
