"""Reading and driving a page the app itself hosts.

The agent's accessibility dump of a WebView on a secondary display comes back
as one empty node -- measured, and the reason this module exists. Everything
here goes through `evaluateJavascript` instead, which returns the page's own
answer: exact text, real structure, and clicks that name their target.

Calls are started and polled, never awaited: a Java call that blocks holds the
interpreter, and a page that never finishes loading would freeze the agent.
"""

from __future__ import annotations

import json
import time

#: Longest page text handed to the model in one go.
MAX_TEXT = 6000

#: What a block page says. Kept deliberately narrow: searching for "验证码"
#: produces a page whose title contains that word, and treating that as a wall
#: would hand every such search to the user.
BLOCK_TITLES = (
    "just a moment", "attention required", "access denied", "安全验证",
    "人机验证", "forbidden", "访问受限", "异常访问",
)
#: Phrases a verification wall puts at the top of the page, where a search
#: result's snippets do not reach.
BLOCK_TEXTS = (
    "拖动滑块", "拖动下方滑块", "请完成验证", "完成安全验证", "人机身份验证",
    "verify you are human", "checking your browser", "unusual traffic",
    "are you a robot", "please complete the security check", "访问过于频繁",
)


class Pacer:
    """Space out visits to the same site.

    Hammering a site is the fastest way to get blocked, and a blocked page costs
    a whole task. One interval per host, in memory, is enough for that.
    """

    def __init__(self, min_interval: float = 2.0) -> None:
        self.min_interval = float(min_interval)
        self.last: dict = {}

    def wait(self, url: str, sleep=time.sleep) -> float:
        host = ""
        try:
            from urllib.parse import urlparse

            host = urlparse(url).netloc
        except Exception:  # noqa: BLE001
            host = ""
        if not host:
            return 0.0
        previous = self.last.get(host)
        now = time.monotonic()
        waited = 0.0
        if previous is not None:
            gap = self.min_interval - (now - previous)
            if gap > 0:
                sleep(gap)
                waited = gap
        self.last[host] = time.monotonic()
        return waited


#: Shared by every page controller in the process.
PACER = Pacer()


def _bridge():
    """The Kotlin bridge, or None outside the app."""
    try:
        from java import jclass  # Chaquopy only

        return jclass("com.phoneagent.app.BrowserBridge")
    except Exception:  # noqa: BLE001 - no JVM
        return None


def available() -> bool:
    return _bridge() is not None


#: The page runs inside this app, so it has to be started on the agent's display
#: with an explicit `--display`; `am start` without one reuses the task that is
#: already there.
BROWSER_COMPONENT = "com.phoneagent.app/.BrowserActivity"


def ensure_open(device, url: str, display: int = -1) -> str:
    """Start the browser on the agent's display and load ``url``.

    ``display`` -1 means the agent's own display; 0 puts the page in front of
    the user, which is how a verification wall gets solved.
    """
    if display < 0:
        display = int(getattr(device, "display_id", 0) or 0)
    # Quoted, and single quotes escaped the way a shell wants: a query string
    # ("?a=1&b=2") is otherwise split by the remote shell, which truncates the
    # URL and runs the rest as a command.
    quoted = "'" + str(url).replace("'", "'\\''") + "'"
    try:
        device.shell(f"am start --display {display} -n {BROWSER_COMPONENT} --es url {quoted}")
    except Exception as exc:  # noqa: BLE001 - reported by the caller
        print(f"[web] could not start the browser: {exc}")
        return ""
    page = WebPage()
    return page.open(url)


def page() -> "WebPage":
    """The page controller for the browser the agent is driving."""
    return WebPage()


class WebPage:
    """A thin wrapper over BrowserBridge with a bounded wait."""

    def __init__(self, timeout: float = 15.0, sleep=time.sleep, bridge=None) -> None:
        self.bridge = bridge if bridge is not None else _bridge()
        self.timeout = timeout
        self.sleep = sleep

    # -- plumbing --------------------------------------------------------

    def eval(self, js: str, timeout: float = 0.0) -> str:
        """Run JS and return its JSON-encoded result ("" on timeout)."""
        if self.bridge is None:
            return ""
        tag = int(self.bridge.beginEval(js))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            value = str(self.bridge.pollEval(tag))
            if value:
                if value.startswith("\u0000"):
                    return ""
                return value
            if time.monotonic() >= deadline:
                # Nothing will read this tag now; without the discard the
                # bridge keeps one string per timed-out call for the life of the
                # process.
                discard = getattr(self.bridge, "discardEval", None)
                if discard is not None:
                    try:
                        discard(tag)
                    except Exception:  # noqa: BLE001 - a leak we cannot fix here
                        pass
                return ""
            self.sleep(0.05)

    def open(self, url: str, timeout: float = 0.0) -> str:
        """Load a URL and wait for the page to finish; returns the final URL."""
        if self.bridge is None:
            return ""
        PACER.wait(url)
        self.bridge.beginLoad(url)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            state = str(self.bridge.pollLoad())
            if state:
                return state
            if time.monotonic() >= deadline:
                return "timeout"
            self.sleep(0.1)

    def url(self) -> str:
        return str(self.bridge.currentUrl()) if self.bridge else ""

    # -- reading ---------------------------------------------------------

    def text(self, limit: int = MAX_TEXT) -> str:
        """The visible text of the page, whitespace collapsed."""
        raw = self.eval(
            "(function(){var t=document.body?document.body.innerText:'';"
            "return t.replace(/\\s+/g,' ').trim();})()"
        )
        return self._decode(raw, "")[:limit]

    def links(self, limit: int = 40) -> list:
        """Anchor texts with their hrefs, for choosing where to go next."""
        raw = self.eval(
            "Array.prototype.slice.call(document.links,0,%d)"
            ".map(function(a){return {text:(a.innerText||'').trim().slice(0,40),"
            "href:a.href}}).filter(function(l){return l.text})" % limit
        )
        value = self._decode(raw, [])
        return value if isinstance(value, list) else []

    def state(self) -> dict:
        """Where the page is: url, scroll position, text length.

        A WebView's accessibility tree is one empty node, so it is identical
        before and after a scroll or a navigation -- which makes the agent's
        "the screen did not change" check wrong on every web task. This is the
        signature it needs instead.
        """
        raw = self.eval(
            "(function(){var d=document.scrollingElement||document.documentElement;"
            "return {url: location.href, y: Math.round(d?d.scrollTop:0),"
            " n: (document.body?document.body.innerText:'').length};})()"
        )
        value = self._decode(raw, {})
        return value if isinstance(value, dict) else {}

    def blocked(self) -> str:
        """The reason this page is a verification wall, or "" if it is not.

        A block page is not a failure to load: it is the site asking for a
        person. Reading it as a normal page wastes a whole task, which is why
        it is detected rather than parsed.
        """
        raw = self.eval(
            "(function(){var b=document.body?document.body.innerText:'';"
            "return {title: document.title||'', text: b.slice(0,600)};})()"
        )
        facts = self._decode(raw, {})
        if not isinstance(facts, dict):
            return ""
        title = str(facts.get("title") or "").lower()
        body = str(facts.get("text") or "").lower()
        for word in BLOCK_TITLES:
            if word.lower() in title:
                return f"title says {word!r}"
        for word in BLOCK_TEXTS:
            if word.lower() in body:
                return f"page says {word!r}"
        return ""

    def network(self, limit: int = 10, wait: float = 2.0) -> list:
        """The JSON the page fetched, newest last.

        Each entry is {url, status, body} with the body clipped to 4 KB. It is
        often the structured version of what the page renders, which is why it
        is worth reading instead of the visible text.
        """
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            raw = self.eval("window.__paNet||[]")
            value = self._decode(raw, [])
            if isinstance(value, list) and value:
                return value[-limit:]
            if time.monotonic() >= deadline:
                return []
            self.sleep(0.3)

    def install_capture(self) -> bool:
        """Ask the bridge to wrap this page's fetch/XHR (for pages already open)."""
        install = getattr(self.bridge, "installNetworkCapture", None) if self.bridge else None
        if install is None:
            return False
        try:
            install()
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- acting ----------------------------------------------------------

    #: Clicking by label is what the model asks for, and the order matters: a
    #: submit button that says 百度一下 is the search button, while an <a> that
    #: merely contains those words is a hot-search link (measured: clicking the
    #: link searched for someone else's trending topic).
    _CLICK_JS = (
        "(function(){var want=%(want)s;"
        "function label(e){return ((e.innerText||e.value||'')+'').trim()}"
        "var controls=Array.prototype.slice.call("
        "document.querySelectorAll('input[type=submit],button,input[type=button],[role=button]'));"
        "var links=Array.prototype.slice.call(document.querySelectorAll('a'));"
        "var passes=[controls,links];"
        "for(var p=0;p<passes.length;p++){"
        "  var els=passes[p];"
        "  for(var i=0;i<els.length;i++){if(label(els[i])===want){els[i].click();return 'ok';}}"
        "}"
        "for(var p=0;p<passes.length;p++){"
        "  var els=passes[p];"
        "  for(var i=0;i<els.length;i++){if(label(els[i]).indexOf(want)>=0){els[i].click();return 'ok';}}"
        "}"
        "return 'not-found';})()"
    )

    def click_text(self, text: str) -> bool:
        """Click the control whose label is ``text``, exact match first."""
        js = self._CLICK_JS % {"want": json.dumps(text, ensure_ascii=False)}
        return self._decode(self.eval(js), "") == "ok"

    #: Setting `value` alone leaves the site's own model empty: measured on
    #: Baidu, where the box showed DeepSeek but the search went to a trending
    #: topic because their JS never saw a keystroke. insertText goes through
    #: the editing pipeline, which is what those frameworks listen to.
    _TYPE_JS = (
        "(function(){var e=document.querySelector(%(sel)s);"
        "if(!e) return 'not-found';"
        "e.focus();"
        "try{e.setSelectionRange(0,(e.value||'').length);}catch(err){}"
        "var done=false;"
        "try{done=document.execCommand('insertText',false,%(val)s);}catch(err){done=false;}"
        "if(!done){e.value=%(val)s;}"
        "e.dispatchEvent(new Event('input',{bubbles:true}));"
        "e.dispatchEvent(new Event('change',{bubbles:true}));"
        "return (e.value||'')? 'ok':'ok-empty';})()"
    )

    def type_into(self, selector: str, value: str) -> bool:
        js = self._TYPE_JS % {
            "sel": json.dumps(selector),
            "val": json.dumps(value, ensure_ascii=False),
        }
        return self._decode(self.eval(js), "").startswith("ok")

    #: A page can scroll through a container, through CSS smooth scrolling, or
    #: not at all because it already sits at the bottom. Touching scrollTop on
    #: the scrolling element with behavior forced to auto is the version that
    #: actually moves: measured on baike.baidu.com, where window.scrollBy left
    #: the position untouched.
    _SCROLL_JS = (
        "(function(){var d=document.scrollingElement||document.documentElement;"
        "try{d.style.scrollBehavior='auto';}catch(e){}"
        "var before=d.scrollTop;"
        "var target=before+window.innerHeight*%(pages)s;"
        "d.scrollTop=target;"
        "if(d.scrollTop===before){window.scrollBy(0,window.innerHeight*%(pages)s);}"
        "return Math.round(d.scrollTop);})()"
    )

    def scroll(self, pages: float = 1.0) -> int:
        """Scroll by ``pages`` screens; returns the new scroll position."""
        js = self._SCROLL_JS % {"pages": float(pages)}
        value = self._decode(self.eval(js), 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def title(self) -> str:
        return str(self._decode(self.eval("document.title||''"), ""))

    @staticmethod
    def _decode(raw: str, default):
        """evaluateJavascript answers with JSON; unwrap exactly once."""
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default
