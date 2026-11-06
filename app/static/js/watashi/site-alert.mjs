// 全ページ共通のエラー表示。ページ本文の枠（#site-content）の上の枠（#site-alert）に出す。
// 各ページは、メッセージを出すときに notify() を使う。エラーは上の枠、それ以外（途中経過・成功）は
// これまでどおり、ページ内の表示欄に出す。上の枠がないページ（ログイン・新規登録など）では、
// エラーも表示欄に出す。

// root: document（試験では模擬の root）、statusNode: そのページの表示欄（なければ null）。
export function notify(root, statusNode, text, error = false) {
  const region = root?.querySelector?.("#site-alert") || null;
  const label = region ? root.querySelector("#site-alert-text") : null;
  if (error && region && label) {
    label.textContent = text;
    region.hidden = false;
    if (statusNode) {
      statusNode.textContent = "";
      statusNode.classList?.toggle("is-error", false);
    }
    return;
  }
  // 新しい結果が出たら、前のエラー表示は消す。
  if (region) region.hidden = true;
  if (statusNode) {
    statusNode.textContent = text;
    statusNode.classList?.toggle("is-error", error);
  }
}

export function clearSiteAlert(root) {
  const region = root?.querySelector?.("#site-alert");
  if (region) region.hidden = true;
}

// 閉じるボタンと、どこにも拾われなかったエラーの表示。ページを開いたとき、1回だけ呼ぶ。
export function bindSiteAlert(root, win) {
  const region = root?.querySelector?.("#site-alert");
  const close = root?.querySelector?.("#site-alert-close");
  if (!region || !close || region.dataset?.bound === "true") return;
  region.dataset.bound = "true";
  close.addEventListener("click", () => { region.hidden = true; });
  if (!win?.addEventListener) return;
  // 画面の処理で拾われなかった失敗は、黙って消えないよう、上の枠に出す。
  const unexpected = (reason) => {
    // 画面の移動で通信が中断されたときは、利用者に見せる必要がない。
    if (reason?.name === "AbortError") return;
    notify(root, null, "予期しないエラーが起きました。ページを開き直してから、もう一度お試しください。", true);
  };
  win.addEventListener("unhandledrejection", (event) => unexpected(event.reason));
  win.addEventListener("error", (event) => unexpected(event.error));
}

if (typeof document !== "undefined" && typeof window !== "undefined") bindSiteAlert(document, window);
