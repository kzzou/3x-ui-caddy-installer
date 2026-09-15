"""Create a real Xray client config from the installer's generated VLESS URI."""
import json
import sys
import urllib.parse
from pathlib import Path

node_file, listen_port, output_file = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
node = json.loads(node_file.read_text())


def find_vless(value):
    if isinstance(value, str) and value.startswith("vless://"):
        return value
    nested_values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for nested in nested_values:
        found = find_vless(nested)
        if found:
            return found
    return None


uri = find_vless(node)
if not uri:
    raise SystemExit("generated node JSON does not contain a VLESS URI")
parsed = urllib.parse.urlsplit(uri)
query = urllib.parse.parse_qs(parsed.query)


def required(name):
    try:
        return query[name][0]
    except (KeyError, IndexError):
        raise SystemExit(f"generated VLESS URI lacks {name}") from None


if parsed.port != 443:
    raise SystemExit(f"generated VLESS URI must publish port 443, got {parsed.port}")
config = {
    "log": {"loglevel": "warning"},
    "inbounds": [{
        "listen": "127.0.0.1", "port": listen_port, "protocol": "socks",
        "settings": {"auth": "noauth", "udp": False},
    }],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{
            "address": "127.0.0.1", "port": parsed.port,
            "users": [{"id": parsed.username, "encryption": "none", "flow": required("flow")}],
        }]},
        "streamSettings": {
            "network": required("type"), "security": "reality",
            "realitySettings": {
                "fingerprint": required("fp"), "serverName": required("sni"),
                # Xray 26 renamed the client-side publicKey field to password.
                "password": required("pbk"), "shortId": required("sid"),
                "spiderX": query.get("spx", [""])[0],
            },
        },
    }],
}
output_file.write_text(json.dumps(config, indent=2) + "\n")
