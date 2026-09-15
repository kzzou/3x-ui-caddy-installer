"""Exercise migration control flow in a temporary filesystem with mocked services.

No host /etc or /root is touched, and no packages or services are installed.
Database/file backup and restore operations use real SQLite/filesystem APIs.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / "install-3x-ui-caddy.sh"

for failure in ("none", "stop", "configure", "start", "https"):
    with tempfile.TemporaryDirectory(prefix="xui-migrate-test-") as tmp:
        root = Path(tmp)
        script = SOURCE.read_text()
        for old, new in (
            ("/etc/x-ui", root / "db"),
            ("/etc/caddy", root / "caddy"),
            ("/etc/systemd/system", root / "units"),
            ("/usr/local/x-ui", root / "app"),
            ("/root/", str(root / "root") + "/"),
            ("/tmp/3x-ui-caddy.", str(root / "work.")),
        ):
            script = script.replace(old, str(new))
        for folder in ("db", "caddy", "units", "app/bin", "root"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        installer = root / "installer.sh"
        installer.write_text(script)
        xui = root / "app/x-ui"
        xui.write_text("#!/bin/sh\nprintf '3.8.0\\n'\n")
        xui.chmod(0o755)
        xray = root / "app/bin/xray-linux-amd64"
        xray.write_text("#!/bin/sh\nexit 0\n")
        xray.chmod(0o755)
        (root / "units/x-ui.service").write_text("Description=3x-ui panel (Caddy reverse proxy)\n")
        old_caddy = "# original site\n"
        (root / "caddy/Caddyfile").write_text(old_caddy)
        node = root / "root/3x-ui-reality-node.json"
        node.write_text('{"original":true}\n')
        settings = {"webListen": "127.0.0.1", "webPort": "2053", "subListen": "127.0.0.1",
                    "subPort": "2096", "webDomain": "panel.example.com", "subDomain": "panel.example.com",
                    "webCertFile": "", "webKeyFile": "", "subCertFile": "", "subKeyFile": ""}
        for key, path in (("webBasePath", "/panel-test/"), ("subPath", "/sub-test/"),
                          ("subJsonPath", "/json-test/"), ("subClashPath", "/clash-test/")):
            settings[key] = path
        for key, path in (("subURI", "/sub-test/"), ("subJsonURI", "/json-test/"), ("subClashURI", "/clash-test/")):
            settings[key] = "https://panel.example.com" + path
        db = root / "db/x-ui.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
            conn.executemany("INSERT INTO settings VALUES (?, ?)", settings.items())
        harness = r'''
source "$1"
DOMAIN=panel.example.com
XRAY_BINARY="$TEST_DIR/app/bin/xray-linux-amd64"
systemctl() {
    printf '%s\n' "$*" >> "$TEST_DIR/service.log"
    if [[ "$1" == is-active ]]; then printf 'active\n'; fi
    if [[ "$FAILURE" == stop && "$*" == 'stop x-ui caddy' ]]; then
        # Model Caddy stopping successfully while x-ui refuses to stop.
        touch "$TEST_DIR/caddy-stopped"
        return 1
    fi
    if [[ "$*" == 'start caddy' ]]; then rm -f "$TEST_DIR/caddy-stopped"; fi
    if [[ "$FAILURE" == start && "$*" == 'start x-ui' && ! -f "$TEST_DIR/failed-once" ]]; then
        touch "$TEST_DIR/failed-once"
        return 1
    fi
}
caddy() { if [[ "$1" == adapt ]]; then printf '{}\n'; fi; }
install() { cp "${@: -2:1}" "${@: -1}"; }
configure_reality_inbound() {
    python3 - "$1" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute("INSERT INTO settings VALUES ('test-mutation', 'yes')")
PY
    printf '{"sub_id":"test"}\n' > "$4"
    [[ "$FAILURE" != configure ]]
}
wait_panel() { :; }
check_migration_ports() { :; }
check_shared_listeners() { :; }
wait_public_https() { [[ "$FAILURE" != https ]]; }
migrate_existing
'''
        # Port-owner preflight runs ss through Python; shadow ss as an empty host.
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        ss = fake_bin / "ss"
        ss.write_text("#!/bin/sh\nexit 0\n")
        ss.chmod(0o755)
        result = subprocess.run(["bash", "-c", harness, "test", str(installer)],
                                env={**os.environ, "TEST_DIR": tmp, "FAILURE": failure,
                                     "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
                                capture_output=True, text=True)
        assert (result.returncode == 0) == (failure == "none"), (failure, result.stdout, result.stderr)
        with sqlite3.connect(db) as conn:
            actual = dict(conn.execute("SELECT key, value FROM settings"))
        if failure == "none":
            assert actual.pop("test-mutation") == "yes"
            assert "https://panel.example.com:8443" in (root / "caddy/Caddyfile").read_text()
            assert json.loads(node.read_text())["subscription_url"].endswith("/sub-test/test")
        else:
            assert (root / "caddy/Caddyfile").read_text() == old_caddy
            assert json.loads(node.read_text()) == {"original": True}
        assert actual == settings, failure
        service_log = (root / "service.log").read_text()
        assert "enable" not in service_log and "disable" not in service_log
        if failure != "none":
            assert service_log.endswith("start caddy\nstart x-ui\n"), service_log
        if failure == "stop":
            assert service_log.count("stop x-ui caddy") == 1, service_log
            assert not (root / "caddy-stopped").exists(), "Caddy must restart after a partial stop"
        print("PASS: migration", failure)
