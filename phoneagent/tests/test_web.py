"""The page controller's decoding, without a JVM."""

from __future__ import annotations

from phoneagent.web import WebPage, available


def test_there_is_no_bridge_on_the_host():
    # Everything must degrade to "nothing" rather than raise outside the app.
    assert available() is False
    page = WebPage()
    assert page.text() == ""
    assert page.links() == []
    assert page.click_text("x") is False
    assert page.title() == ""
    assert page.open("https://example.com") == ""


def test_decode_unwraps_the_engines_json_exactly_once():
    # evaluateJavascript returns JSON; a page string arrives quoted, and a page
    # that returns a quoted string arrives quoted twice. Only one layer is ours.
    assert WebPage._decode('"hello"', "") == "hello"
    assert WebPage._decode('"\\"ok\\""', "") == '"ok"'
    assert WebPage._decode("[]", []) == []
    assert WebPage._decode("", "fallback") == "fallback"
    assert WebPage._decode("not json", "fallback") == "fallback"


def test_a_failed_eval_reports_nothing():
    assert WebPage._decode("\u0000no-webview", "") == ""


def test_click_prefers_a_control_over_a_link():
    """The search button says 百度一下; so does a hot-search link."""
    js = WebPage._CLICK_JS % {"want": '"百度一下"'}
    # submit inputs first, then other controls, then links
    assert js.index("input[type=submit]") < js.index("button,input[type=button]")
    assert js.index("button,input[type=button]") < js.index("querySelectorAll('a')")
    # and an exact label before a partial one
    assert js.index("===want") < js.index("indexOf(want)")


def test_typing_goes_through_the_editing_pipeline():
    """Setting .value left Baidu's model empty; insertText is what it listens to."""
    js = WebPage._TYPE_JS % {"sel": '"#kw"', "val": '"DeepSeek"'}
    assert "execCommand('insertText'" in js
    assert "dispatchEvent(new Event('input'" in js
    assert "e.value=" in js          # still the fallback


def test_network_reads_what_the_page_fetched():
    class Bridge:
        def beginEval(self, js):
            return 1

        def pollEval(self, tag):
            return '[{"url":"https://api/x","status":200,"body":"{\\"a\\":1}"}]'

        def beginLoad(self, url):
            self._url = url

        def pollLoad(self):
            return getattr(self, "_url", "")

        def currentUrl(self):
            return "https://example.com/"

    entries = WebPage(bridge=Bridge()).network()
    assert entries[0]["url"] == "https://api/x"
    assert entries[0]["status"] == 200


def test_network_without_a_bridge_is_empty():
    assert WebPage(bridge=None).network() == []


class _PageBridge:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def beginEval(self, js):
        return 1

    def pollEval(self, tag):
        return self.payload

    def beginLoad(self, url):
        pass

    def pollLoad(self):
        return ""

    def currentUrl(self):
        return ""


def test_a_captcha_page_is_recognised():
    page = WebPage(bridge=_PageBridge('{"title":"安全验证","text":"请拖动滑块完成验证"}'))
    assert page.blocked()


def test_a_normal_search_for_captcha_words_is_not_a_wall():
    # searching for 验证码 gives a page whose title contains the word
    page = WebPage(bridge=_PageBridge('{"title":"验证码_百度搜索","text":"为您找到相关结果"}'))
    assert page.blocked() == ""


def test_a_page_without_signals_is_not_blocked():
    page = WebPage(bridge=_PageBridge('{"title":"Example Domain","text":"hello"}'))
    assert page.blocked() == ""


def test_the_pacer_spaces_out_the_same_host():
    from phoneagent.web import Pacer

    slept = []
    pacer = Pacer(min_interval=2.0)
    pacer.last["example.com"] = 0.0
    import time as _time

    pacer.last["example.com"] = _time.monotonic()
    pacer.wait("https://example.com/a", sleep=slept.append)
    assert slept and slept[0] > 0
    # a different host is not delayed
    slept.clear()
    pacer.wait("https://other.test/a", sleep=slept.append)
    assert slept == []


def test_a_url_with_a_query_string_survives_the_shell():
    """?a=1&b=2 used to be split by the remote shell, truncating the URL."""
    from phoneagent.web import ensure_open

    class Device:
        display_id = 7

        def __init__(self):
            self.commands = []

        def shell(self, command):
            self.commands.append(command)
            return ""

    device = Device()
    ensure_open(device, "https://example.com/s?q=a&page=2")

    command = device.commands[0]
    assert "'https://example.com/s?q=a&page=2'" in command
    assert "--display 7" in command


def test_a_timed_out_eval_is_discarded():
    """Otherwise the bridge keeps one string per timed-out call, forever."""
    from phoneagent.web import WebPage

    class Bridge:
        def __init__(self):
            self.discarded = []

        def beginEval(self, js):
            return 42

        def pollEval(self, tag):
            return ""                      # never answers

        def discardEval(self, tag):
            self.discarded.append(tag)

    bridge = Bridge()
    page = WebPage(bridge=bridge, timeout=0.01, sleep=lambda _s: None)
    assert page.eval("1+1", timeout=0.01) == ""
    assert bridge.discarded == [42]
