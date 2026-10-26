"""Rendered shared-menu contracts, including strict checks for damaged markup."""

import re
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.extensions import db
from tests.test_protection import register


VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})
MENU_SCRIPT = "/static/js/watashi/menu.mjs"
HEADER_SCRIPT = "/static/js/watashi/header-session.mjs"
NAV_LABELS = {
    "/": "ホーム", "/answers": "回答", "/sharing": "共有",
    "/family": "家族", "/transfer": "移行", "/health": "起動確認",
}
NAV_HREFS = tuple(NAV_LABELS)
APPLICATION_MENU_ROLES = {"application", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio"}
SHARED_PAGES = (
    ("/", 200, "/"),
    ("/answers", 200, "/answers"),
    ("/answers/demo", 200, "/answers"),
    ("/sharing", 200, "/sharing"),
    ("/family", 200, "/family"),
    ("/family/invite", 200, "/family"),
    ("/transfer", 200, "/transfer"),
    ("/view/layout-check", 200, None),
    ("/missing-menu-layout", 404, None),
)

# HTMLParser tolerates broken attributes, even when they swallow closing tags.
START_TAG = re.compile(
    r"""<[A-Za-z][A-Za-z0-9:-]*
        (?:\s+[^\s\"'<>/=]+
            (?:\s*=\s*(?:\"[^\"<]*\"|'[^'<]*'|[^\s\"'`=<>]+))?
        )*
        \s*/?>
    """,
    re.VERBOSE,
)


def only(elements, description):
    assert len(elements) == 1, f"Expected one {description}, found {len(elements)}"
    return elements[0]


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict
    ancestors: tuple
    namespace: str = "html"
    text: str = ""


class StrictMarkup(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.elements = []
        self.open_elements = []
        self.feed(body)
        self.close()
        assert not self.open_elements, (
            f"Unclosed elements: {[element.tag for element in self.open_elements]}"
        )

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        assert START_TAG.fullmatch(raw), f"Malformed start tag at {self.getpos()}: {raw}"
        names = [name for name, _value in attrs]
        assert len(names) == len(set(names)), f"Duplicate attributes: {raw}"
        parent = self.open_elements[-1] if self.open_elements else None
        in_svg = tag == "svg" or (
            parent is not None and parent.namespace == "svg"
            and parent.tag not in {"foreignobject", "desc", "title"}
        )
        namespace = "svg" if in_svg else "html"
        element = Element(tag, dict(attrs), tuple(self.open_elements), namespace=namespace)
        self.elements.append(element)
        if namespace == "svg" or tag not in VOID_TAGS:
            self.open_elements.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        element = self.elements[-1]
        assert tag in VOID_TAGS or element.namespace == "svg", (
            f"Non-void HTML element cannot self-close: <{tag}/>"
        )
        if element.namespace == "svg":
            self.handle_endtag(tag)

    def handle_data(self, data):
        for element in self.open_elements:
            element.text += data

    def handle_endtag(self, tag):
        assert self.open_elements and self.open_elements[-1].tag == tag, (
            f"Unexpected </{tag}> at {self.getpos()}; "
            f"open elements: {[element.tag for element in self.open_elements]}"
        )
        self.open_elements.pop()

    def tagged(self, tag):
        return [element for element in self.elements if element.tag == tag]

    def one_id(self, element_id):
        return only(
            [element for element in self.elements if element.attrs.get("id") == element_id],
            f"#{element_id}",
        )

    def module_script(self, path):
        script = only(
            [element for element in self.tagged("script")
             if urlsplit(element.attrs.get("src") or "").path == path],
            f"script loading {path}",
        )
        assert script.attrs.get("type") == "module"
        return script


def assert_decorative_svg(markup, container):
    icon = only(
        [element for element in markup.tagged("svg") if container in element.ancestors],
        f"decorative SVG inside {container.attrs.get('id') or container.attrs.get('href')}",
    )
    # HTMLParser lowercases SVG attribute names, including viewBox.
    for name, expected in {
        "width": "24", "height": "24", "viewbox": "0 0 24 24",
        "aria-hidden": "true", "focusable": "false",
    }.items():
        assert icon.attrs.get(name) == expected, f"SVG {name} must be {expected!r}"


def assert_shared_layout(markup, current_href, *, account_page=False):
    body = only(markup.tagged("body"), "body")
    assert "site-body" in (body.attrs.get("class") or "").split()
    header = markup.one_id("site-header")
    assert header is only(markup.tagged("header"), "header")
    toggle = markup.one_id("menu-toggle")
    account = markup.one_id("header-account")
    account_link = markup.one_id("header-account-link")
    for element in (toggle, account, account_link):
        assert header in element.ancestors, f"#{element.attrs['id']} must be inside the header"
    assert account in account_link.ancestors
    assert account_link.tag == "a"
    assert account_link.attrs.get("data-account-url") == "/account"
    assert account_link.attrs.get("aria-current") == ("page" if account_page else None)
    for element_id in ("header-session-label", "header-session-name"):
        assert account in markup.one_id(element_id).ancestors
    assert not any(header in nav.ancestors for nav in markup.tagged("nav"))

    layout = markup.one_id("site-layout")
    backdrop = markup.one_id("menu-backdrop")
    menu = markup.one_id("site-menu")
    content = markup.one_id("site-content")
    for element in (backdrop, menu, content):
        assert element.ancestors and element.ancestors[-1] is layout, (
            f"#{element.attrs['id']} must be a direct child of #site-layout"
        )
    assert header not in layout.ancestors
    assert menu.tag == "aside"
    assert header not in menu.ancestors
    assert menu.attrs.get("aria-labelledby") == "site-menu-title"
    assert menu in markup.one_id("site-menu-title").ancestors
    menu_account = markup.one_id("menu-account")
    assert menu in menu_account.ancestors
    for element_id in ("menu-session-label", "menu-session-name", "menu-account-link"):
        assert menu_account in markup.one_id(element_id).ancestors
    menu_account_link = markup.one_id("menu-account-link")
    assert menu_account_link.attrs["href"] == account_link.attrs["href"]
    assert "data-auth-link" in menu_account_link.attrs
    assert menu_account_link.attrs.get("aria-current") == ("page" if account_page else None)

    close = markup.one_id("menu-close")
    assert menu in close.ancestors
    for control, label in ((toggle, "メニューを開く"), (close, "メニューを閉じる")):
        assert control.tag == "button"
        assert control.attrs.get("type") == "button"
        assert "hidden" in control.attrs, "JS-only controls must be hidden without JavaScript"
        assert control.attrs.get("aria-label") == label
        assert_decorative_svg(markup, control)
    assert toggle.attrs.get("aria-controls") == "site-menu"
    assert toggle.attrs.get("aria-expanded") == "false"
    for element in (*menu.ancestors, menu):
        assert "hidden" not in element.attrs, "The no-JS sidebar must remain visible"
        assert "inert" not in element.attrs, "The no-JS sidebar must remain usable"
        assert element.attrs.get("aria-hidden") != "true"

    nav = only(
        [element for element in markup.tagged("nav") if menu in element.ancestors],
        "sidebar navigation",
    )
    links = [element for element in markup.tagged("a") if nav in element.ancestors]
    assert sorted(link.attrs.get("href") or "" for link in links) == sorted(NAV_HREFS)
    for link in links:
        assert_decorative_svg(markup, link)
        label = only(
            [element for element in markup.tagged("span") if link in element.ancestors
             and "nav-label" in (element.attrs.get("class") or "").split()],
            f"nav-label span for {link.attrs['href']}",
        )
        assert label.text.strip() == NAV_LABELS[link.attrs["href"]]
        for element in (*label.ancestors, label):
            assert "hidden" not in element.attrs, "Navigation labels must remain visible"
            assert element.attrs.get("aria-hidden") != "true"
    current_links = [
        (link.attrs.get("href"), link.attrs.get("aria-current"))
        for link in links if link.attrs.get("aria-current") not in (None, "false")
    ]
    assert current_links == ([] if current_href is None else [(current_href, "page")])

    main = only(markup.tagged("main"), "main")
    assert main is markup.one_id("main")
    for element in markup.elements:
        if main not in element.ancestors:
            roles = (element.attrs.get("role") or "").lower().split()
            assert not APPLICATION_MENU_ROLES.intersection(roles), "Use navigation, not application-menu roles"
    footer = only(markup.tagged("footer"), "footer")
    banner = only(
        [element for element in markup.elements
         if "banner" in (element.attrs.get("class") or "").split()],
        "development banner",
    )
    for element in (main, footer, banner):
        assert content in element.ancestors, f"{element.tag} must be inside #site-content"
    markup.module_script(MENU_SCRIPT)
    markup.module_script(HEADER_SCRIPT)


@pytest.mark.parametrize("path,status,current_href", SHARED_PAGES, ids=[page[0] for page in SHARED_PAGES])
def test_common_pages_have_strict_shared_menu_layout(client, path, status, current_href):
    response = client.get(path)
    assert response.status_code == status
    assert_shared_layout(StrictMarkup(response.get_data(as_text=True)), current_href)


def test_shared_shell_has_no_remote_asset_urls(client):
    response = client.get("/")
    assert response.status_code == 200
    markup = StrictMarkup(response.get_data(as_text=True))
    main = markup.one_id("main")
    for element in markup.elements:
        if main in element.ancestors:
            continue
        for attribute in ("src", "href", "xlink:href", "srcset"):
            if element.tag == "a" and attribute == "href":
                continue
            value = element.attrs.get(attribute) or ""
            urls = value.split(",") if attribute == "srcset" else [value]
            for url in urls:
                parsed = urlsplit(url.strip())
                assert parsed.scheme not in {"http", "https"} and not parsed.netloc, (
                    f"Remote shared-shell asset: <{element.tag} {attribute}={value!r}>"
                )


@pytest.fixture
def authenticated_client(app, client):
    with app.app_context():
        db.create_all()
    register(client, "メニューのレイアウト検証")
    return client


def test_authenticated_account_has_strict_shared_menu_layout(authenticated_client):
    response = authenticated_client.get("/account")
    assert response.status_code == 200
    markup = StrictMarkup(response.get_data(as_text=True))
    assert_shared_layout(markup, None, account_page=True)
    assert markup.one_id("main") in markup.one_id("account-settings").ancestors


def test_auth_remains_standalone_without_shared_menu(client):
    response = client.get("/auth")
    assert response.status_code == 200
    markup = StrictMarkup(response.get_data(as_text=True))
    assert not markup.tagged("header")
    shared_ids = {
        "site-header", "menu-toggle", "header-account", "header-account-link",
        "site-layout", "menu-backdrop", "site-menu", "site-menu-title", "menu-close",
        "site-content", "header-session-label", "header-session-name",
    }
    assert not shared_ids.intersection(element.attrs.get("id") for element in markup.elements)
    script_paths = {
        urlsplit(element.attrs.get("src") or "").path for element in markup.tagged("script")
    }
    assert MENU_SCRIPT not in script_paths
    assert HEADER_SCRIPT not in script_paths
    assert markup.one_id("main") in markup.one_id("auth-entry").ancestors
    markup.module_script("/static/js/watashi/auth-entry.mjs")


@pytest.mark.parametrize("body", [
    "<header><div></header></div>",
    "<header></button></header>",
    "<header><button></button>",
    '<header><a class="brand"</button> href="/">Brand</a></header>',
    '<aside><nav a</button>ria-label="主要"></nav></aside>',
    '<header><button aria-label="Close </header>"></button></header>',
    "<header><button /></header>",
    '<header id="site-header" id="other"></header>',
    "<header><svg><g></svg></g></header>",
    "<header><svg></svg><button /></header>",
    "<svg><foreignObject><button /></foreignObject></svg>",
], ids=[
    "misnested", "stray-close", "unclosed", "damaged-brand-attributes",
    "damaged-nav-attributes", "closing-tag-inside-attribute", "nonvoid-self-close",
    "duplicate-attribute", "misnested-svg", "nonvoid-self-close-after-svg",
    "nonvoid-self-close-in-svg-html-content",
])
def test_strict_markup_rejects_malformed_html(body):
    with pytest.raises(AssertionError):
        StrictMarkup(body)


def test_strict_markup_accepts_void_tags_without_closing_tags():
    for tag in sorted(VOID_TAGS):
        StrictMarkup(f"<div><{tag}><{tag} /></div>")


def test_strict_markup_accepts_nested_inline_svg_and_self_closing_shapes():
    markup = StrictMarkup("""<button><svg xmlns="http://www.w3.org/2000/svg"
      width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <g><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="12" cy="12" r="2" /></g>
      </svg></button>""")
    assert_decorative_svg(markup, only(markup.tagged("button"), "button"))


def test_menu_behavior():
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "tests/menu.mjs"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_strict_markup_accepts_raw_script_and_style_contents():
    StrictMarkup("""<!DOCTYPE html><html><head>
      <style>.example::before { content: '<header></button>'; }</style>
      </head><body><script type="module">
        if (1 < 2 && 3 > 2) {
          const fragment = `<header><button></button></header>`;
          const notAnEndTag = '</button>';
        }
      </script></body></html>""")
