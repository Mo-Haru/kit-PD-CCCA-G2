// 広い画面で閉じていたメニューを、ページを移動しても閉じたままにする。
// 表示の前（head）で、ちらつかないよう、閉じていたことだけを印に付ける。保存と更新は menu.mjs が行う。
try {
  if (window.localStorage.getItem("menuOpen") === "false") document.documentElement.dataset.menuPref = "closed";
} catch (_error) { /* 保存できない環境では、開いた状態から始める。 */ }
