import * as client from "./client.mjs";
import { notify } from "./site-alert.mjs";

// わたしノート・進捗表示画面（沖担当の画面を統合）。
// 回答は、ブラウザ内で復号した回答文書から読む（localStorage には置かない）。入力は回答の画面（/answers）で行う。

// '2026-10-14' → '2026年10月14日'
export function formatNoteDate(dateText) {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText || "");
  return parts ? `${parts[1]}年${Number(parts[2])}月${Number(parts[3])}日` : "";
}

export function noteProgress(total, answered) {
  const remaining = total - answered;
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((answered / total) * 100))) : 0;
  const text = total === 0 ? "質問は、まだ準備中です。"
    : answered === 0 ? `${total}問中、まだ回答していません。最初の質問から答えてみましょう。`
      : remaining > 0 ? `${total}問中${answered}問に回答済みです。あと${remaining}問で完了です。`
        : "すべての質問に回答しました。おつかれさまでした。";
  return { percent, text };
}

export function renderNote(root, items) {
  const cards = [...root.querySelectorAll("[data-note-id]")];
  let answered = 0;
  for (const card of cards) {
    const item = items?.[card.dataset.noteId];
    const value = item?.state === "answered" && typeof item.value === "string" ? item.value : "";
    if (value) answered += 1;
    const date = formatNoteDate(item?.answered_at);
    card.querySelector(".note-item-date").textContent = value ? (date ? `${date}に回答` : "回答済み") : "まだ回答していません";
    const answer = card.querySelector(".note-answer");
    answer.textContent = value || "未回答";
    answer.classList.toggle("note-answer--hidden", !value);
    card.querySelector(".note-actions a").textContent = value ? "書き直す" : "回答する";
  }
  const progress = noteProgress(cards.length, answered);
  root.getElementById("note-percent").textContent = String(progress.percent);
  root.getElementById("note-bar").setAttribute("aria-valuenow", String(progress.percent));
  root.getElementById("note-bar-fill").style.width = `${progress.percent}%`;
  root.getElementById("note-progress-text").textContent = progress.text;
  return progress;
}

export function bindNotePage(root, api = client) {
  const node = id => root.getElementById(id);
  let busy = false;
  function show(text, error = false) { notify(root, node("status"), text, error); }

  async function load() {
    const before = await api.api("/api/v1/auth/session");
    const loaded = await api.loadAnswerDocument();
    const after = await api.api("/api/v1/auth/session");
    if (before.user_id !== after.user_id) throw new Error("アカウントが切り替わりました。ページを開き直してください。");
    renderNote(root, loaded.document?.items || {});
    node("unlock-here").hidden = true;
    show(loaded.revision === 0 ? "保存済みの回答はありません。「回答する」から始めましょう。" : "保存済みの回答を復号して表示しています。");
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    node("unlock-here").disabled = true;
    try { await action(); }
    catch (error) {
      show(["NotAllowedError", "AbortError"].includes(error.name)
        ? "暗号化の解除を完了できませんでした。「回答の暗号化を解除」でもう一度お試しください。"
        : error.message || "回答を読み込めませんでした。", true);
    } finally { busy = false; node("unlock-here").disabled = false; }
  }
  async function unlockAndLoad() {
    show("回答の暗号化を解除しています…");
    await api.unlock();
    await load();
  }

  node("unlock-here").addEventListener("click", () => run(unlockAndLoad));
  return run(async () => {
    const session = await api.api("/api/v1/auth/session");
    node("login-here").hidden = session.authenticated;
    node("unlock-here").hidden = !session.authenticated;
    if (!session.authenticated) {
      show("ログインすると、回答の進み具合を確認できます。");
      return;
    }
    if (api.getUnlockState().unlocked) await load();
    else await unlockAndLoad();
  });
}

if (typeof document !== "undefined" && document.querySelector("[data-note-page]")) bindNotePage(document);
