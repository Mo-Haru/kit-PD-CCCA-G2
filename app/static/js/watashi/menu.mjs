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
  let desktopOpen = true;
  let open = wideScreen.matches;

  function render({ focusMenu = false, restoreFocus = false } = {}) {
    const wide = wideScreen.matches;
    const modal = open && !wide;
    const focusInMenu = menu.contains(document.activeElement);
    const focusOnClose = document.activeElement === closeButton;

    // Release the background before returning focus to the header button.
    for (const element of background) element.inert = false;
    toggle.hidden = false;
    if (restoreFocus || (!open && focusInMenu) || (wide && focusOnClose)) toggle.focus();

    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "メニューを閉じる" : "メニューを開く");
    closeButton.hidden = wide;
    // Keep the drawer rendered so CSS can animate it; inert removes closed links from focus.
    menu.hidden = false;
    menu.inert = !open;
    if (open) menu.removeAttribute("aria-hidden");
    else menu.setAttribute("aria-hidden", "true");

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
    if (wideScreen.matches) desktopOpen = value;
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
