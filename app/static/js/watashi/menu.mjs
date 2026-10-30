const toggle = document.querySelector("#menu-toggle");
const menu = document.querySelector("#site-menu");
const layout = document.querySelector("#site-layout");
const closeButton = document.querySelector("#menu-close");
const backdrop = document.querySelector("#menu-backdrop");

if (toggle && menu && layout && closeButton && backdrop) {
  const wideScreen = window.matchMedia("(min-width: 960px)");
  const background = [
    document.querySelector(".skip"),
    document.querySelector("#site-header"),
    document.querySelector("#site-content"),
  ].filter(Boolean);
  // 広い画面の開閉は、ページを移動しても保つ。保存できない環境では、開いた状態から始める。
  const readPreference = () => {
    try { return window.localStorage.getItem("menuOpen") !== "false"; } catch (_error) { return true; }
  };
  const savePreference = (value) => {
    try { window.localStorage.setItem("menuOpen", String(value)); } catch (_error) { /* 保存できなくても、開閉はできる。 */ }
  };
  let desktopOpen = readPreference();
  let open = wideScreen.matches ? desktopOpen : false;

  function render({ focusMenu = false, restoreFocus = false } = {}) {
    const wide = wideScreen.matches;
    const modal = open && !wide;
    const focusInMenu = menu.contains(document.activeElement);
    const focusOnClose = document.activeElement === closeButton;

    // Release the background before returning focus to the header button.
    for (const element of background) element.inert = false;
    toggle.hidden = false;
    // 広い画面では、閉じてもレール（アイコン）に操作できる項目が残るので、フォーカスは動かさない。
    if (restoreFocus || (!open && !wide && focusInMenu) || (wide && focusOnClose)) toggle.focus();

    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "メニューを閉じる" : "メニューを開く");
    closeButton.hidden = wide;
    // Keep the drawer rendered so CSS can animate it; inert removes closed links from focus.
    // Wide screens keep an icon-only rail when closed, so the links stay usable there.
    const railOnly = !open && wide;
    menu.hidden = false;
    menu.inert = !open && !wide;
    if (open || wide) menu.removeAttribute("aria-hidden");
    else menu.setAttribute("aria-hidden", "true");
    // The rail shows icons only; the label becomes a tooltip.
    for (const link of menu.querySelectorAll("a[href]")) {
      const label = link.querySelector(".nav-label");
      if (!label) continue;
      if (railOnly) link.setAttribute("title", label.textContent.trim());
      else link.removeAttribute("title");
    }

    if (modal) {
      menu.setAttribute("role", "dialog");
      menu.setAttribute("aria-modal", "true");
    } else {
      menu.removeAttribute("role");
      menu.removeAttribute("aria-modal");
    }
    layout.dataset.menuReady = "true";
    layout.dataset.menuOpen = String(open);
    backdrop.hidden = !modal;
    document.documentElement.classList.toggle("menu-modal-open", modal);

    if (modal && (focusMenu || !menu.contains(document.activeElement))) closeButton.focus();
    for (const element of background) element.inert = modal;
  }

  function setOpen(value, options) {
    open = value;
    if (wideScreen.matches) {
      desktopOpen = value;
      savePreference(value);
    }
    render(options);
  }

  toggle.addEventListener("click", () => {
    setOpen(!open, { focusMenu: !wideScreen.matches });
  });
  closeButton.addEventListener("click", () => setOpen(false, { restoreFocus: true }));
  backdrop.addEventListener("click", () => setOpen(false, { restoreFocus: true }));
  menu.addEventListener("click", (event) => {
    if (!wideScreen.matches && event.target.closest("a[href]")) {
      setOpen(false, { restoreFocus: true });
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!open) return;
    if (event.key === "Escape") {
      if (!wideScreen.matches || menu.contains(document.activeElement) || document.activeElement === toggle) {
        event.preventDefault();
        setOpen(false, { restoreFocus: true });
      }
      return;
    }
    if (event.key !== "Tab" || wideScreen.matches) return;

    const items = [...menu.querySelectorAll("a[href], button:not([disabled])")]
      .filter((element) => !element.hidden);
    const first = items[0];
    const last = items[items.length - 1];
    const outside = !menu.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || outside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || outside)) {
      event.preventDefault();
      first.focus();
    }
  });

  wideScreen.addEventListener("change", () => {
    open = wideScreen.matches ? desktopOpen : false;
    render();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      open = wideScreen.matches ? desktopOpen : false;
      render();
    }
  });
  render();
  document.body.classList.add("has-fixed-layout");
}
