#!/usr/bin/env bash
# Real amd64 Linux integration: Xray REALITY :443 -> Caddy TLS :8443 -> 3x-ui.
set -Eeuo pipefail
TEST_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../install-3x-ui-caddy.sh
source "$TEST_ROOT/install-3x-ui-caddy.sh"
TEST_DIR=$(mktemp -d /tmp/xui-caddy-smoke.XXXXXXXX)
XUI_PID='' CADDY_PID='' CLIENT_PID='' HTTP_PID='' SERVER_XRAY_PID='' IPV6_PID=''
finish() {
    local status=$?
    trap - EXIT
    for pid in "$CLIENT_PID" "$SERVER_XRAY_PID" "$XUI_PID" "$CADDY_PID" "$HTTP_PID" "$IPV6_PID"; do
        [[ -z "$pid" ]] || kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    printf '\nTest artifacts: %s\n' "$TEST_DIR"
    if (( status != 0 )); then tail -n 80 "$TEST_DIR"/*.log 2>/dev/null || true; fi
    exit "$status"
}
trap finish EXIT
[[ "$(uname -m)" == x86_64 ]] || die 'smoke test requires amd64 Linux'
for command in curl python3 openssl ss tar sha256sum; do
    command -v "$command" >/dev/null || die "smoke test requires $command"
done

download 'https://github.com/MHSanaei/3x-ui/releases/download/v3.8.0/x-ui-linux-amd64.tar.gz' "$TEST_DIR/x-ui.tar.gz"
printf '%s  %s\n' 236b837627520f0c4ae4134dc6a34ea5e294b69e158879795fe8cd51c5f3582c "$TEST_DIR/x-ui.tar.gz" | sha256sum -c
download 'https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz' "$TEST_DIR/caddy.tar.gz"
printf '%s  %s\n' 527fbf917c39189a1e3b31d34fa955601680b2d5c8055d2a87b8b9588dec7bb9 "$TEST_DIR/caddy.tar.gz" | sha256sum -c
tar -xzf "$TEST_DIR/x-ui.tar.gz" -C "$TEST_DIR"
tar -xzf "$TEST_DIR/caddy.tar.gz" -C "$TEST_DIR" caddy

DOMAIN=panel.example.com
PANEL_PATH=/panel-test SUB_PATH=/sub-test JSON_PATH=/json-test CLASH_PATH=/clash-test
export XUI_DB_FOLDER="$TEST_DIR/db" XUI_DB_TYPE=sqlite
export XUI_LOG_FOLDER="$TEST_DIR/log" XUI_BIN_FOLDER="$TEST_DIR/x-ui/bin"
export XDG_DATA_HOME="$TEST_DIR/xdg-data" XDG_CONFIG_HOME="$TEST_DIR/xdg-config"
mkdir -p "$XUI_DB_FOLDER" "$XUI_LOG_FOLDER"
chmod +x "$TEST_DIR/x-ui/x-ui" "$TEST_DIR/caddy" "$TEST_DIR/x-ui/bin/"xray-linux-*
XRAY=$(find "$TEST_DIR/x-ui/bin" -maxdepth 1 -type f -name 'xray-linux-*' -print -quit)
for port in 80 443 2053 2096 8443 18081 18082 18083; do
    port_busy "$port" && die "test port $port occupied"
done

cd "$TEST_DIR/x-ui"
./x-ui setting -username testadmin -password test-secret-123456789 \
    -listenIP 127.0.0.1 -port 2053 -webBasePath /panel-test/ > "$TEST_DIR/init.log" 2>&1
configure_database "$XUI_DB_FOLDER/x-ui.db"
configure_reality_inbound "$XUI_DB_FOLDER/x-ui.db" "$DOMAIN" "$XRAY" "$TEST_DIR/node.json"
# Keep even the simulated public listener local during the integration test.
python3 - "$XUI_DB_FOLDER/x-ui.db" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute("UPDATE inbounds SET listen = '127.0.0.1' WHERE port = 443")
PY

# Record the actual Xray/Go wildcard behavior independently of the fixture.
cat > "$TEST_DIR/ipv6-probe.json" <<'EOF'
{"log":{"loglevel":"none"},"inbounds":[{"listen":"0.0.0.0","port":18083,"protocol":"socks","settings":{"auth":"noauth","udp":false}}],"outbounds":[{"protocol":"freedom"}]}
EOF
"$XRAY" run -config "$TEST_DIR/ipv6-probe.json" > "$TEST_DIR/ipv6-probe.log" 2>&1 &
IPV6_PID=$!
for _ in {1..20}; do port_busy 18083 && break; sleep 0.1; done
if python3 - <<'PY'
import socket
with socket.create_connection(("::1", 18083), timeout=1):
    pass
PY
then
    printf 'INFO: Xray listen 0.0.0.0 is reachable via ::1 (dual stack)\n'
else
    printf 'INFO: Xray listen 0.0.0.0 is IPv4-only; ::1 was refused\n'
fi
kill "$IPV6_PID"
wait "$IPV6_PID" 2>/dev/null || true
IPV6_PID=''

# Production configuration must remain valid before replacing ACME with a test-local CA.
render_caddyfile > "$TEST_DIR/Caddyfile.production"
"$TEST_DIR/caddy" validate --config "$TEST_DIR/Caddyfile.production" --adapter caddyfile
python3 - "$TEST_DIR/Caddyfile.production" "$TEST_DIR/Caddyfile" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
text = text.replace("{\n    auto_https", "{\n    admin off\n    skip_install_trust\n    auto_https", 1)
text = text.replace("http://panel.example.com {", "http://panel.example.com {\n    bind 127.0.0.1", 1)
text = re.sub(r"    tls \{\n        issuer acme \{\n            disable_tlsalpn_challenge\n        \}\n    \}", "    tls internal", text)
Path(sys.argv[2]).write_text(text)
PY
"$TEST_DIR/caddy" validate --config "$TEST_DIR/Caddyfile" --adapter caddyfile

# A deterministic local destination proves bytes traverse the client and server Xray pair.
mkdir "$TEST_DIR/http-root"
printf 'REALITY transport reached the target\n' > "$TEST_DIR/http-root/probe.txt"
python3 -m http.server 18081 --bind 127.0.0.1 --directory "$TEST_DIR/http-root" > "$TEST_DIR/http.log" 2>&1 &
HTTP_PID=$!
"$TEST_DIR/caddy" run --config "$TEST_DIR/Caddyfile" --adapter caddyfile > "$TEST_DIR/caddy.log" 2>&1 &
CADDY_PID=$!
./x-ui > "$TEST_DIR/x-ui.log" 2>&1 &
XUI_PID=$!

CA_FILE="$XDG_DATA_HOME/caddy/pki/authorities/local/root.crt"
for _ in {1..40}; do [[ -s "$CA_FILE" ]] && break; sleep 0.25; done
[[ -s "$CA_FILE" ]] || die 'Caddy local CA was not created'
python3 "$TEST_ROOT/tests/verify.py" "$TEST_DIR" "$DOMAIN" 443 "$CA_FILE" \
    "$PANEL_PATH" "$SUB_PATH" "$JSON_PATH" "$CLASH_PATH"

# 3x-ui's normal template blocks private destinations. For the transport-only
# check, replace its child with the same generated config after removing that
# policy, allowing the deterministic target to stay on 127.0.0.1.
XRAY_CHILD=$(pgrep -P "$XUI_PID" -f 'xray-linux-' | head -n 1)
[[ -n "$XRAY_CHILD" ]] || die 'could not find the 3x-ui Xray child'
kill "$XRAY_CHILD"
for _ in {1..40}; do kill -0 "$XRAY_CHILD" 2>/dev/null || break; sleep 0.1; done
python3 - "$TEST_DIR/x-ui/bin/config.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
config = json.loads(path.read_text())
config["routing"]["rules"] = [rule for rule in config["routing"]["rules"]
                              if "geoip:private" not in rule.get("ip", [])]
for outbound in config["outbounds"]:
    final_rules = outbound.get("settings", {}).get("finalRules")
    if final_rules:
        outbound["settings"]["finalRules"] = [rule for rule in final_rules
                                                if "geoip:private" not in rule.get("ip", [])]
path.write_text(json.dumps(config, indent=2) + "\n")
PY
"$XRAY" run -config "$TEST_DIR/x-ui/bin/config.json" > "$TEST_DIR/server-xray.log" 2>&1 &
SERVER_XRAY_PID=$!
python3 "$TEST_ROOT/tests/reality-transport.py" "$TEST_DIR/node.json" 18082 "$TEST_DIR/client.json"
"$XRAY" run -config "$TEST_DIR/client.json" > "$TEST_DIR/client-xray.log" 2>&1 &
CLIENT_PID=$!
for _ in {1..40}; do
    if curl --noproxy '' --fail --silent --show-error --max-time 2 \
        --socks5-hostname 127.0.0.1:18082 http://127.0.0.1:18081/probe.txt \
        --output "$TEST_DIR/proxied.txt" 2>/dev/null; then break; fi
    sleep 0.25
done
cmp "$TEST_DIR/http-root/probe.txt" "$TEST_DIR/proxied.txt"

check_loopback 2053
check_loopback 2096
[[ "$(ss -H -ltn 'sport = :8443' | awk '{print $4}')" == '127.0.0.1:8443' ]] || die 'Caddy 8443 escaped loopback'
[[ "$(ss -H -ltn 'sport = :443' | awk '{print $4}')" == '127.0.0.1:443' ]] || die 'test Xray 443 escaped loopback'
printf '\nPASS: real Xray REALITY 443, Caddy TLS fallback, panel, subscription and SOCKS data path\n'
