"""The Desktop's own address in tests (D79): ``http://puffpai.localhost:<port>``.

The Core reads the ``puffpai_device`` cookie, and serves the Desktop's pages, only when ``Host`` is exactly
``puffpai.localhost:<port>``, and the cookie counts only with its second half in ``X-PuffPAI-Device`` (what
the Desktop keeps in its localStorage). ``desktop_client(app)`` is a Flask test client whose requests go to
the Desktop's address by default; ``sign_in`` sets the cookie host-only on that name and binds a second half
to it, as ``POST /v1/pair`` does, and the client then sends that half on every request, as desktop.js does.
"""

from __future__ import annotations

from flask.testing import FlaskClient
from werkzeug.datastructures import Headers

PORT = 8420                                   # create_app's default; nothing is bound in a test client
DESKTOP_URL = f"http://puffpai.localhost:{PORT}"
DESKTOP = {"Origin": DESKTOP_URL, "X-PuffPAI": "1"}     # what desktop.js sends with the device cookie
DEVICE_HEADER = "X-PuffPAI-Device"


class DesktopClient(FlaskClient):
    base_url = DESKTOP_URL
    device_key: str | None = None

    def open(self, *args, **kwargs):
        if not args or isinstance(args[0], str):
            kwargs.setdefault("base_url", self.base_url)
            if self.device_key:
                headers = Headers(kwargs.get("headers") or {})
                if DEVICE_HEADER not in headers:
                    headers.add(DEVICE_HEADER, self.device_key)
                kwargs["headers"] = headers
        return super().open(*args, **kwargs)

    def sign_in(self, token: str) -> str:
        """The cookie, and (for a paired device) its second half, as after ``POST /v1/pair``."""
        self.set_cookie("puffpai_device", token, domain="puffpai.localhost")
        self.device_key = self.application.extensions["puffpai"].tokens.bind_device(token)
        return token


def desktop_client(app, port: int = PORT) -> DesktopClient:
    client = DesktopClient(app, app.response_class, use_cookies=True)
    client.base_url = f"http://puffpai.localhost:{port}"
    return client


def desktop_headers(port: int = PORT) -> dict:
    return {"Origin": f"http://puffpai.localhost:{port}", "X-PuffPAI": "1"}
