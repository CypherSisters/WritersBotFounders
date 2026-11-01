"""No test ever opens her real browser (D81, lane window).

A test browser stub that failed once let Python's ``webbrowser`` go on to her default browser, which opened local
test tabs and redeemed a scratch sign-in link there, on the same ``puffpai.localhost`` cookie as her real dashboard.
So ``webbrowser`` is cut off at the top, in the test process (``conftest``) and in every Core a test starts
(``console_core``, ``window_demo_core``). A test that wants to see what would open passes its own function or
patches over this one. ``opened`` stays empty unless a test does that.

Her default browser's program is off too, even headless: the page reader (``zone2.page_reader``, behind ``read_app``)
would start it with a temp profile, so ``browser_path`` says none here and ``read_app`` falls to its plain read. Real
users are not touched: only a process that calls :func:`install` is.
"""

import webbrowser

opened = []


def _refuse(url="", *_a, **_k):
    opened.append(str(url))
    raise AssertionError("a test tried to open her real browser")


def _no_browser_object(*_a, **_k):
    raise webbrowser.Error("a test never gets a real browser")


def install() -> None:
    from puffpai_core.zone2 import page_reader
    page_reader.browser_path = lambda: None
    webbrowser.open = _refuse
    webbrowser.open_new = _refuse
    webbrowser.open_new_tab = _refuse
    webbrowser.get = _no_browser_object
    webbrowser.register = lambda *_a, **_k: None
    webbrowser._tryorder = []                 # what it would have tried, if anything still reached it
    webbrowser._browsers = {}
