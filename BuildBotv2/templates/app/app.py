"""__NAME__: the smallest app, a page and a health address, standard library only. It refuses every request that does not
carry its own PUFFPAI_TOKEN (02 "PUFFPAI_TOKEN", D48d). The Core adds the token when it forwards her page."""

import hmac
import http.server
import os
import pathlib

DATA = pathlib.Path(os.environ["PUFFPAI_DATA"])
DATA.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("PUFFPAI_TOKEN", "")


class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not TOKEN or not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TOKEN}"):
            self.send_response(401)
            self.end_headers()
            return
        body = b"ok" if self.path == "/health" else b"<!doctype html><title>__NAME__</title><h1>__NAME__</h1><p>Your page goes here.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if self.path == "/health" else "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PUFFPAI_PORT"])), Page).serve_forever()
