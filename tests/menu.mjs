import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { createContext, SourceTextModule } from "node:vm";

const sourceUrl = new URL("../app/static/js/watashi/menu.mjs", import.meta.url);
const source = await readFile(sourceUrl, "utf8");

class Events {
  listeners = new Map();

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type, properties = {}) {
    const event = {
      type, target: this, bubbles: false, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      ...properties,
    };
    for (let current = this; current; current = event.bubbles ? current.parentNode : null) {
      event.currentTarget = current;
      for (const listener of current.listeners.get(type) || []) listener(event);
    }
    return event;
  }
}

class Element extends Events {
  constructor(document, tag, id, attrs = {}) {
    super();
    this.document = document;
    this.tag = tag;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = new Map(Object.entries({ id, ...attrs }));
    const classes = new Set();
    this.classList = {
      add(name) { classes.add(name); },
      contains(name) { return classes.has(name); },
      toggle(name, enabled = !classes.has(name)) {
        if (enabled) classes.add(name);
        else classes.delete(name);
        return enabled;
      },
    };
    for (const name of ["hidden", "inert", "disabled"]) {
      Object.defineProperty(this, name, {
        get: () => this.attributes.has(name),
        set: (value) => {
          if (value) this.attributes.set(name, "");
          else this.attributes.delete(name);
        },
      });
    }
  }

  get id() { return this.getAttribute("id"); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }

  matches(selector) {
    return selector.split(",").some((part) => {
      const value = part.trim();
      if (value.startsWith("#")) return this.id === value.slice(1);
      if (value.startsWith(".")) return this.classList.contains(value.slice(1));
      if (value === "a[href]") return this.tag === "a" && this.attributes.has("href");
      if (value === "button:not([disabled])") return this.tag === "button" && !this.disabled;
      assert.match(value, /^[a-z]+$/, `Unsupported selector: ${value}`);
      return this.tag === value;
    });
  }

  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }

  closest(selector) {
    for (let current = this; current instanceof Element; current = current.parentNode) {
      if (current.matches(selector)) return current;
    }
    return null;
  }

  contains(element) {
    for (let current = element; current; current = current.parentNode) {
      if (current === this) return true;
    }
    return false;
  }

  blocked() {
    for (let current = this; current instanceof Element; current = current.parentNode) {
      if (current.hidden || current.inert || current.disabled) return true;
    }
    return !this.document.documentElement.contains(this);
  }

  focus() {
    // Browsers ignore focus into inert/hidden ancestors; do not make bad cleanup order pass.
    if (this.blocked()) this.document.blockedFocus.push(this.id);
    else this.document.activeElement = this;
  }

  click() {
    assert.equal(this.blocked(), false, `Cannot click hidden/inert #${this.id}`);
    this.closest("a[href], button")?.focus();
    return this.emit("click", { bubbles: true });
  }

  remove() {
    this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = null;
  }
}

async function scenario({ wide = true, missing = [], stored, storage = "ok" } = {}) {
  const document = new Events();
  document.children = [];
  document.blockedFocus = [];
  document.querySelector = Element.prototype.querySelector;
  document.querySelectorAll = Element.prototype.querySelectorAll;
  const add = (parent, tag, id, attrs) => {
    const element = new Element(document, tag, id, attrs);
    parent.children.push(element);
    element.parentNode = parent;
    return element;
  };
  document.documentElement = add(document, "html", "root");
  document.body = add(document.documentElement, "body", "body");
  document.activeElement = document.body;
  const skip = add(document.body, "a", "skip-link", { href: "#main" });
  skip.classList.add("skip");
  const header = add(document.body, "header", "site-header");
  const toggle = add(header, "button", "menu-toggle", {
    hidden: "", "aria-controls": "site-menu", "aria-expanded": "false",
  });
  const accountLink = add(header, "a", "header-account-link", { href: "/account" });
  const layout = add(document.body, "div", "site-layout");
  const backdrop = add(layout, "div", "menu-backdrop", { hidden: "" });
  const menu = add(layout, "aside", "site-menu", { "aria-labelledby": "site-menu-title" });
  const title = add(menu, "h2", "site-menu-title");
  const close = add(menu, "button", "menu-close", { hidden: "" });
  const menuAccountLink = add(menu, "a", "menu-account-link", { href: "/account" });
  const nav = add(menu, "nav", "navigation");
  const links = ["/", "/family", "/transfer"].map((href, index) => (
    add(nav, "a", `link-${index}`, { href })
  ));
  const linkLabel = add(links[0], "span", "link-label");
  add(nav, "button", "disabled-item", { disabled: "" });
  add(nav, "a", "hidden-item", { href: "/hidden", hidden: "" });
  const content = add(layout, "div", "site-content");
  const outside = add(content, "button", "content-action");

  for (const id of missing) {
    if (id === "all") {
      for (const child of [...document.body.children]) child.remove();
    } else {
      document.querySelector(`#${id}`).remove();
    }
  }

  const media = new Events();
  media.matches = wide;
  media.media = "(min-width: 960px)";
  const mediaQueries = [];
  const window = new Events();
  window.matchMedia = (query) => {
    mediaQueries.push(query);
    assert.equal(query, media.media);
    return media;
  };
  // ページを移動しても保つ、広い画面の開閉。保存できない環境も試す。
  const saved = new Map(stored === undefined ? [] : [["menuOpen", stored]]);
  if (storage === "throws") {
    Object.defineProperty(window, "localStorage", { get() { throw new Error("blocked"); } });
  } else {
    window.localStorage = { getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => { saved.set(key, value); } };
  }
  const context = createContext({ document, window });
  const entry = new SourceTextModule(source, { context, identifier: sourceUrl.href });
  await entry.link((specifier) => assert.fail(`Unexpected menu dependency: ${specifier}`));
  await entry.evaluate({ timeout: 1000 });
  return {
    document, window, media, mediaQueries, saved, toggle, menu, layout, close, backdrop,
    accountLink, menuAccountLink, title, links, linkLabel, outside,
    background: [skip, header, content].filter((element) => document.documentElement.contains(element)),
    setWide(value) {
      if (media.matches === value) return;
      media.matches = value;
      media.emit("change", { matches: value, media: media.media });
    },
    key(key, shiftKey = false) {
      return document.emit("keydown", { key, shiftKey, target: document.activeElement });
    },
  };
}

function assertFocused(page, element) {
  assert.equal(page.document.activeElement.id, element.id, "focused element");
}

function assertState(page, open) {
  const wide = page.media.matches;
  const modal = open && !wide;
  assert.equal(page.layout.dataset.menuReady, "true");
  assert.equal(page.layout.dataset.menuOpen, String(open));
  assert.equal(page.toggle.hidden, false);
  assert.equal(page.toggle.getAttribute("aria-expanded"), String(open));
  assert.equal(page.close.hidden, wide);
  assert.equal(page.menu.hidden, false, "closed menus must remain rendered for the slide animation");
  // 広い画面では、閉じても、アイコンだけのレールが残る（操作できる）。
  assert.equal(page.menu.inert, !open && !wide);
  assert.equal(page.menu.getAttribute("aria-hidden"), open || wide ? null : "true");
  assert.equal(page.menu.getAttribute("aria-labelledby"), "site-menu-title");
  assert.equal(page.menu.getAttribute("role"), modal ? "dialog" : null);
  assert.equal(page.menu.getAttribute("aria-modal"), modal ? "true" : null);
  assert.equal(page.backdrop.hidden, !modal);
  assert.equal(page.document.documentElement.classList.contains("menu-modal-open"), modal);
  for (const element of page.background) assert.equal(element.inert, modal, `#${element.id} inert`);
  assert.deepEqual(page.document.blockedFocus, [], "focus must never target hidden/inert elements");
}

function openDrawer(page) {
  page.toggle.click();
  assertState(page, true);
  assertFocused(page, page.close);
}

await test("desktop starts expanded and toggles without modal side effects", async () => {
  const page = await scenario();
  assertState(page, true);
  assertFocused(page, page.document.body);
  page.toggle.click();
  assertState(page, false);
  assertFocused(page, page.toggle);
  page.toggle.click();
  assertState(page, true);
  assertFocused(page, page.toggle);
});

await test("desktop closed menu keeps an icon-only rail whose links stay usable", async () => {
  const page = await scenario();
  page.linkLabel.classList.add("nav-label");
  page.linkLabel.textContent = "ホーム";
  page.toggle.click();
  assertState(page, false);
  assert.equal(page.menu.inert, false, "rail links must stay reachable");
  assert.equal(page.links[0].getAttribute("title"), "ホーム", "the label becomes a tooltip");
  page.links[1].focus();
  page.toggle.click();
  assertState(page, true);
  assert.equal(page.links[0].getAttribute("title"), null);
  // 狭い画面で閉じたドロワーは、これまでどおり操作できない。
  page.setWide(false);
  assertState(page, false);
  assert.equal(page.menu.inert, true);
  assert.equal(page.links[0].getAttribute("title"), null);
});

await test("desktop open/closed choice is saved and survives moving to another page", async () => {
  const first = await scenario();
  first.toggle.click();
  assert.equal(first.saved.get("menuOpen"), "false");
  // 次のページ：保存した「閉じた」から始まる。
  const closed = await scenario({ stored: first.saved.get("menuOpen") });
  assertState(closed, false);
  closed.toggle.click();
  assert.equal(closed.saved.get("menuOpen"), "true");
  assertState(await scenario({ stored: closed.saved.get("menuOpen") }), true);
});

await test("a collapsed desktop choice does not open or close the narrow drawer", async () => {
  const narrow = await scenario({ wide: false, stored: "false" });
  assertState(narrow, false);
  openDrawer(narrow);
  assert.equal(narrow.saved.get("menuOpen"), "false", "the drawer must not overwrite the desktop choice");
  narrow.setWide(true);
  assertState(narrow, false);
});

await test("menu still works when storage is blocked", async () => {
  const page = await scenario({ storage: "throws" });
  assertState(page, true);
  page.toggle.click();
  assertState(page, false);
});

await test("narrow initial state is closed without stealing focus or locking the background", async () => {
  const page = await scenario({ wide: false });
  assertState(page, false);
  assertFocused(page, page.document.body);
  assert.equal(page.key("Escape").defaultPrevented, false);
  assert.equal(page.key("Tab").defaultPrevented, false);
});

for (const action of ["close button", "backdrop", "Escape"]) {
  await test(`mobile ${action} releases dialog, inert and scroll lock before restoring focus`, async () => {
    const page = await scenario({ wide: false });
    openDrawer(page);
    page.links[1].focus();
    if (action === "close button") page.close.click();
    else if (action === "backdrop") page.backdrop.click();
    else assert.equal(page.key("Escape").defaultPrevented, true);
    assertState(page, false);
    assertFocused(page, page.toggle);
    openDrawer(page);
    page.close.click();
    assertState(page, false);
    assertFocused(page, page.toggle);
  });
}

await test("mobile Tab wraps both ends, ignores hidden/disabled items and leaves interior Tab native", async () => {
  const page = await scenario({ wide: false });
  openDrawer(page);
  assert.equal(page.key("Tab", true).defaultPrevented, true);
  assertFocused(page, page.links.at(-1));
  assert.equal(page.key("Tab").defaultPrevented, true);
  assertFocused(page, page.close);
  page.links[1].focus();
  assert.equal(page.key("Tab").defaultPrevented, false);
  assert.equal(page.key("Tab", true).defaultPrevented, false);
});

await test("mobile Tab recovers focus drift into the drawer in either direction", async () => {
  const page = await scenario({ wide: false });
  openDrawer(page);
  // Simulate UA/restoration focus drift, not an allowed user focus into inert content.
  page.document.activeElement = page.document.body;
  assert.equal(page.key("Tab").defaultPrevented, true);
  assertFocused(page, page.close);
  page.document.activeElement = page.document.body;
  assert.equal(page.key("Tab", true).defaultPrevented, true);
  assertFocused(page, page.links.at(-1));
});

await test("only mobile link clicks close the menu, including clicks on nested link text", async () => {
  const mobile = await scenario({ wide: false });
  openDrawer(mobile);
  mobile.title.click();
  assertState(mobile, true);
  assert.equal(mobile.linkLabel.click().defaultPrevented, false);
  assertState(mobile, false);
  assertFocused(mobile, mobile.toggle);
  openDrawer(mobile);
  mobile.menuAccountLink.click();
  assertState(mobile, false);
  assertFocused(mobile, mobile.toggle);
  const desktop = await scenario();
  assert.equal(desktop.linkLabel.click().defaultPrevented, false);
  assertState(desktop, true);
  assertFocused(desktop, desktop.links[0]);
});

await test("Escape outside the desktop menu neither collapses it nor consumes the key", async () => {
  const page = await scenario();
  for (const outside of [page.outside, page.accountLink]) {
    outside.focus();
    assert.equal(page.key("Escape").defaultPrevented, false);
    assert.equal(page.key("Tab").defaultPrevented, false);
    assertState(page, true);
    assertFocused(page, outside);
  }
});

await test("desktop Tab is not trapped and Escape within the menu collapses and restores focus", async () => {
  const page = await scenario();
  page.links.at(-1).focus();
  assert.equal(page.key("Tab").defaultPrevented, false);
  assert.equal(page.key("Escape").defaultPrevented, true);
  assertState(page, false);
  assertFocused(page, page.toggle);
});

for (const desktopOpen of [true, false]) {
  await test(`breakpoint changes restore desktop ${desktopOpen ? "expanded" : "collapsed"} preference and clean focus`, async () => {
    const page = await scenario();
    if (!desktopOpen) page.toggle.click();
    if (desktopOpen) page.links[0].focus();
    else page.outside.focus();
    page.setWide(false);
    assertState(page, false);
    assertFocused(page, desktopOpen ? page.toggle : page.outside);
    openDrawer(page);
    page.setWide(true);
    assertState(page, desktopOpen);
    assertFocused(page, page.toggle);
    page.setWide(false);
    assertState(page, false);
    page.setWide(true);
    assertState(page, desktopOpen);
  });
}

await test("expanding to desktop preserves valid link focus but narrowing releases hidden menu focus", async () => {
  const page = await scenario({ wide: false });
  openDrawer(page);
  page.links[1].focus();
  page.setWide(true);
  assertState(page, true);
  assertFocused(page, page.links[1]);
  page.setWide(false);
  assertState(page, false);
  assertFocused(page, page.toggle);
});

await test("BFCache pageshow closes a mobile dialog and cleans up; ordinary pageshow does not", async () => {
  const page = await scenario({ wide: false });
  openDrawer(page);
  page.links[1].focus();
  page.window.emit("pageshow", { persisted: false });
  assertState(page, true);
  assertFocused(page, page.links[1]);
  page.window.emit("pageshow", { persisted: true });
  assertState(page, false);
  assertFocused(page, page.toggle);
});

for (const desktopOpen of [true, false]) {
  await test(`BFCache restores desktop ${desktopOpen ? "expanded" : "collapsed"} preference after a suspended resize`, async () => {
    const page = await scenario();
    if (!desktopOpen) page.toggle.click();
    page.setWide(false);
    openDrawer(page);
    // A frozen page can resume at a new width before a media-query change is delivered.
    page.media.matches = true;
    page.window.emit("pageshow", { persisted: true });
    assertState(page, desktopOpen);
    assertFocused(page, page.toggle);
  });
}

for (const missing of ["menu-toggle", "site-menu", "site-layout", "menu-close", "menu-backdrop", "all"]) {
  await test(`missing ${missing} leaves the no-JS fallback untouched and installs no listeners`, async () => {
    const page = await scenario({ missing: [missing] });
    assert.deepEqual(page.mediaQueries, []);
    for (const target of [page.document, page.window, page.media, page.toggle, page.menu, page.close, page.backdrop]) {
      assert.equal(target.listeners.size, 0);
    }
    assert.deepEqual(page.layout.dataset, {});
    assert.equal(page.toggle.hidden, true);
    assert.equal(page.close.hidden, true);
    assert.equal(page.backdrop.hidden, true);
    assert.equal(page.menu.hidden, false);
    assert.equal(page.menu.inert, false);
    assert.equal(page.document.documentElement.classList.contains("menu-modal-open"), false);
    assertFocused(page, page.document.body);
  });
}

for (const missing of ["skip-link", "site-content"]) {
  await test(`missing optional background #${missing} does not break drawer cleanup`, async () => {
    const page = await scenario({ wide: false, missing: [missing] });
    openDrawer(page);
    page.close.click();
    assertState(page, false);
    assertFocused(page, page.toggle);
  });
}
