#!/usr/bin/env bash
# Run on amd64 Linux; all processes and files are scoped to a temporary directory.
set -Eeuo pipefail
TEST_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../install-3x-ui-caddy.sh
source "$TEST_ROOT/install-3x-ui-caddy.sh"
TEST_DIR=$(mktemp -d /tmp/xui-caddy-smoke.XXXXXXXX)
XUI_PID='' CADDY_PID=''
finish() {
    local status=$?
    trap - EXIT
    [[ -z "$CADDY_PID" ]] || kill "$CADDY_PID" 2>/dev/null || true
    [[ -z "$XUI_PID" ]] || kill "$XUI_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    printf '\nTest artifacts: %s\n' "$TEST_DIR"
    if (( status != 0 )); then
        tail -n 50 "$TEST_DIR"/*.log 2>/dev/null || true
    fi
    exit "$status"
}
trap finish EXIT
[[ "$(uname -m)" == x86_64 ]] || die 'smoke test requires amd64 Linux'
for candidate in panel.example.com xn--fiqs8s.example abc.def-123.example; do
    validate_domain "$candidate" || die "valid domain rejected: $candidate"
done
for candidate in 'example.com;id' 'https://example.com' 'a..com' '-x.com' 'x-.com' '127.0.0.1' 'x.com/' 'x.com:443'; do
    if validate_domain "$candidate"; then die "invalid domain accepted: $candidate"; fi
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
for port in 2053 2096 18088; do
    port_busy "$port" && die "test port $port occupied"
done
cd "$TEST_DIR/x-ui"
./x-ui setting -username testadmin -password test-secret-123456789 \
    -listenIP 127.0.0.1 -port 2053 -webBasePath /panel-test/ > "$TEST_DIR/init.log" 2>&1
configure_database "$XUI_DB_FOLDER/x-ui.db"
render_caddyfile > "$TEST_DIR/Caddyfile.production"
"$TEST_DIR/caddy" validate --config "$TEST_DIR/Caddyfile.production" --adapter caddyfile
# Exercise the same handlers without ACME, privileged ports or the admin API.
{ printf '{\n admin off\n auto_https off\n}\n'; render_caddyfile | sed 's/^panel.example.com {/:18088 {\n    bind 127.0.0.1/'; } > "$TEST_DIR/Caddyfile"
./x-ui > "$TEST_DIR/x-ui.log" 2>&1 &
XUI_PID=$!
"$TEST_DIR/caddy" run --config "$TEST_DIR/Caddyfile" --adapter caddyfile > "$TEST_DIR/caddy.log" 2>&1 &
CADDY_PID=$!
python3 "$TEST_ROOT/tests/verify.py" "$TEST_DIR"
verify_login testadmin test-secret-123456789
check_loopback 2053
check_loopback 2096
printf '\nPASS: real binaries, login, routes, subscription settings, loopback bindings\n'
