"""Integration checks against temporary real 3x-ui and Caddy processes."""
import http.cookiejar
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

root = Path(sys.argv[1])
# Only this local HTTP test client accepts Secure cookies over HTTP.
# Production Caddy terminates HTTPS; inspect the actual cookie flag below.
cookies = http.cookiejar.CookieJar(
    policy=http.cookiejar.DefaultCookiePolicy(secure_protocols=("https", "wss", "http"))
)
csrf_token = ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


client = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies), NoRedirect()
)


def request(path, data=None, port=18088):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=urllib.parse.urlencode(data).encode() if data is not None else None,
        headers={"Host": "panel.example.com", "X-CSRF-Token": csrf_token},
    )
    try:
        with client.open(req, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


for attempt in range(30):
    try:
        if request("/panel-test/")[0] == 200:
            break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(1)
else:
    raise AssertionError("Panel did not become ready")

assert request("/") == (404, b"Not Found")
assert request("/panel-test-extra/") == (404, b"Not Found")
assert request("/panel-test")[0] == 308
status, body = request("/panel-test/csrf-token")
assert status == 200, (status, body)
csrf_token = json.loads(body)["obj"]
assert list(cookies) and all(cookie.secure for cookie in cookies), "Missing Secure cookie flag"
status, body = request("/panel-test/login", {"username": "testadmin", "password": "wrong"})
assert status == 200, (status, body)
assert not json.loads(body)["success"], body
status, body = request(
    "/panel-test/login", {"username": "testadmin", "password": "test-secret-123456789"}
)
assert status == 200 and json.loads(body)["success"], body
assert all(cookie.secure for cookie in cookies)

with sqlite3.connect(root / "db/x-ui.db") as conn:
    settings = dict(conn.execute("SELECT key, value FROM settings"))
    stored_password = conn.execute("SELECT password FROM users").fetchone()[0]
    assert stored_password != "test-secret-123456789"
    for key in ("webListen", "subListen"):
        assert settings[key] == "127.0.0.1"
    for key, path in (("subURI", "sub-test"), ("subJsonURI", "json-test"), ("subClashURI", "clash-test")):
        assert settings[key] == f"https://panel.example.com/{path}/"

for prefix in ("sub-test", "json-test", "clash-test"):
    path = f"/{prefix}/no-such-client"
    direct = request(path, port=2096)
    proxied = request(path)
    assert direct[0] == 404, direct
    assert proxied == direct, (proxied, direct)
    assert proxied != (404, b"Not Found"), "Request hit Caddy fallback instead of subscription"

print("PASS: credentials, path preservation, fallback, subscription forwarding and public URLs")
