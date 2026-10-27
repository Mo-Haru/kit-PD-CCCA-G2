import * as client from "./client.mjs";
import { initializeAnswerDemo } from "./answer-demo.mjs";
import { createDemoAnswerStore } from "./answer-demo-store.mjs";

const ids = [...document.querySelectorAll("[data-demo-question]")].map(question => question.dataset.questionId);
const store = createDemoAnswerStore(client, ids);
const status = document.querySelector("#demo-storage-status");
const save = document.querySelector("#demo-save");
const unlock = document.querySelector("#demo-unlock");
const reload = document.querySelector("#demo-load");
const edit = document.querySelector("#demo-edit");
const links = document.querySelector("#demo-sharing-links");
let busy = false;
let ready = false;
let dirty = false;
const ui = initializeAnswerDemo(document, { editable: false, onChange() {
  dirty = true;
  links.hidden = true;
  sync();
} });

function show(text, error = false) {
  status.textContent = text;
  status.classList.toggle("is-error", error);
}

function sync() {
  ui.setEditable(ready && !busy);
  save.disabled = busy || !ready || !dirty;
  edit.disabled = busy;
  unlock.disabled = busy;
  reload.disabled = busy || !ready;
}

async function run(action) {
  if (busy) return;
  busy = true;
  sync();
  try { await action(); }
  catch (error) {
    const message = error.code === "revision_conflict"
      ? "別の画面で回答が更新されています。入力は残しています。内容を控えてから「保存内容を読み直す」で最新版を確認してください。"
      : ["NotAllowedError", "AbortError"].includes(error.name)
        ? "パスキーの操作が完了しませんでした。もう一度お試しください。"
        : error.message || "処理できませんでした。入力は保存していません。";
    show(message, true);
  } finally { busy = false; sync(); }
}

async function load() {
  ready = false;
  const catalog = await client.api("/api/v1/answers/questions");
  if (!ids.every(id => catalog.questions.some(question => question.id === id))) {
    throw new Error("テスト質問がDBに未登録です。開発環境で seed-answer-demo コマンドを実行してください。");
  }
  try {
    const loaded = await store.load();
    ui.restore(loaded.items);
    ready = true;
    dirty = false;
    links.hidden = loaded.revision === 0;
    show(loaded.revision === 0 ? "保存済みの回答はありません。入力して回答内容を確認してください。"
      : `DBの回答を復号して読み込みました（版 ${loaded.revision}）。`);
  } catch (error) { store.invalidate(); throw error; }
}

unlock.addEventListener("click", () => run(async () => {
  show("鍵を解除しています…");
  await client.unlock();
  if (!ready) await load();
  else show("この画面で鍵を解除しました。入力内容はそのままです。");
}));
reload.addEventListener("click", () => run(load));
save.addEventListener("click", () => run(async () => {
  show("回答を暗号化して保存しています…");
  const saved = await store.save(ui.items());
  dirty = false;
  links.hidden = false;
  show(`回答を暗号化してDBに保存しました（版 ${saved.revision}）。回答の更新だけでは既存の共有は変わりません。`);
}));

sync();
client.api("/api/v1/auth/session").then(session => {
  document.querySelector("#demo-login").hidden = session.authenticated;
  unlock.hidden = !session.authenticated;
  show(session.authenticated ? "この画面で鍵を解除すると、保存済みの回答を読み込めます。"
    : "ログイン・新規登録後、この画面で鍵を解除してください。");
}).catch(() => show("ログイン状態を確認できません。ページを開き直してください。", true));
