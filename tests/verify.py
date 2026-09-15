"""Verify the HTTPS fallback path through Xray and Caddy."""
import base64
import http.client
import json
import socket
import sqlite3
import ssl
import sys
import time
import urllib.parse
from http.cookies import SimpleCookie
from pathlib import Path

root = Path(sys.argv[1])
domain = sys.argv[2]
public_port = int(sys.argv[3])
ca_file = sys.argv[4]
panel_path, sub_path, json_path, clash_path = sys.argv[5:9]
csrf_token = ""
cookies = {}
observed_set_cookies = []
tls = ssl.create_default_context(cafile=ca_file)


def request(path, data=None):
    """Make one browser-like HTTPS request to Xray's public REALITY listener."""
    body = urllib.parse.urlencode(data).encode() if data is not None else b""
    headers = {"Host": domain, "Connection": "close", "User-Agent": "xui-caddy-smoke/1"}
    if body:
        headers.update({"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))})
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    if cookies:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    raw = socket.create_connection(("127.0.0.1", public_port), timeout=4)
    secure = tls.wrap_socket(raw, server_hostname=domain)
    connection = http.client.HTTPSConnection(domain, context=tls)
    connection.sock = secure
    connection.request("POST" if data is not None else "GET", path, body=body, headers=headers)
    response = connection.getresponse()
    payload, response_headers = response.read(), response.headers
    for value in response_headers.get_all("Set-Cookie", []):
        observed_set_cookies.append(value)
        parsed = SimpleCookie()
        parsed.load(value)
        for key, morsel in parsed.items():
            cookies[key] = morsel.value
    connection.close()
    return response.status, payload, response_headers


for _ in range(40):
    try:
        if request(panel_path + "/")[0] == 200:
            break
    except (ConnectionError, OSError, ssl.SSLError):
        pass
    time.sleep(0.5)
else:
    raise AssertionError("HTTPS panel did not become ready through the REALITY fallback")

redirect = http.client.HTTPConnection("127.0.0.1", 80, timeout=3)
redirect.request("GET", panel_path + "/?from=http", headers={"Host": domain})
redirect_response = redirect.getresponse()
assert redirect_response.status == 308
assert redirect_response.headers["Location"] == f"https://{domain}{panel_path}/?from=http"
redirect_response.read()
redirect.close()

status, _, headers = request("/")
assert status == 404, status
assert headers.get("Strict-Transport-Security") == "max-age=31536000"
assert "8443" not in headers.get("Alt-Svc", ""), headers.get("Alt-Svc")
assert request(panel_path + "-extra/")[0] == 404
status, _, headers = request(panel_path)
assert status == 308 and headers["Location"].endswith(panel_path + "/"), (status, headers)

status, body, headers = request(panel_path + "/csrf-token")
assert status == 200, (status, body)
csrf_token = json.loads(body)["obj"]
assert observed_set_cookies and all("secure" in value.lower() for value in observed_set_cookies), observed_set_cookies
status, body, _ = request(panel_path + "/login", {"username": "testadmin", "password": "wrong"})
assert status == 200 and not json.loads(body)["success"], body
status, body, headers = request(panel_path + "/login", {"username": "testadmin", "password": "test-secret-123456789"})
assert status == 200 and json.loads(body)["success"], body
assert cookies and all("secure" in value.lower() for value in observed_set_cookies)

with sqlite3.connect(root / "db/x-ui.db") as conn:
    settings = dict(conn.execute("SELECT key, value FROM settings"))
    stored_password = conn.execute("SELECT password FROM users").fetchone()[0]
    inbound_settings = json.loads(conn.execute("SELECT settings FROM inbounds").fetchone()[0])
    subscription_id = inbound_settings["clients"][0]["subId"]
assert stored_password != "test-secret-123456789"
assert settings["webListen"] == "127.0.0.1"
assert settings["subListen"] == "127.0.0.1"
for key, path in (("subURI", sub_path), ("subJsonURI", json_path), ("subClashURI", clash_path)):
    assert settings[key] == f"https://{domain}{path}/"

status, body, _ = request(f"{sub_path}/{subscription_id}")
assert status == 200 and body, (status, body)
compact = b"".join(body.split())
try:
    decoded = base64.b64decode(compact + b"=" * (-len(compact) % 4)).decode()
except (ValueError, UnicodeDecodeError):
    decoded = body.decode()
assert "vless://" in decoded, decoded
assert f"@{domain}:443" in decoded, decoded
print("PASS: browser TLS fallback, panel auth, secure headers and real 443 subscription")
