"""Behavior tests for configure_reality_inbound against the 3x-ui v3.8 schema."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "install-3x-ui-caddy.sh"
PRIVATE = "A" * 43
PUBLIC = "B" * 43


# Column names/defaults are taken from the v3.8.0 GORM models. The normalized
# clients/client_inbounds tables are important: settings.clients alone is no
# longer the authoritative runtime identity source in this release.
SCHEMA = """
CREATE TABLE users (
    id integer PRIMARY KEY AUTOINCREMENT, username text, password text, login_epoch integer DEFAULT 0
);
CREATE TABLE inbounds (
    id integer PRIMARY KEY AUTOINCREMENT, user_id integer, up integer, down integer, total integer,
    remark text, sub_sort_index integer DEFAULT 1, enable numeric, expiry_time integer,
    traffic_reset text DEFAULT 'never', traffic_reset_day integer DEFAULT 1,
    last_traffic_reset_time integer DEFAULT 0, listen text, port integer, protocol text,
    settings text, stream_settings text, tag text UNIQUE, sniffing text, node_id integer,
    share_addr_strategy text DEFAULT 'node', share_addr text, disable_flow numeric DEFAULT false,
    origin_node_guid text
);
CREATE TABLE clients (
    id integer PRIMARY KEY AUTOINCREMENT, email text NOT NULL UNIQUE, sub_id text, uuid text,
    password text, auth text, flow text, security text, reverse text, wg_private_key text,
    wg_public_key text, wg_allowed_ips text, wg_pre_shared_key text, wg_keep_alive integer DEFAULT 0,
    wg_forwarded_ports text, secret text, ad_tag text DEFAULT '', limit_ip integer,
    limit_hwid integer DEFAULT 0, total_gb integer, expiry_time integer, enable numeric DEFAULT true,
    tg_id integer, group_name text DEFAULT '', comment text, reset integer DEFAULT 0,
    reset_day integer DEFAULT 0, reset_max integer DEFAULT 0,
    traffic_reset text DEFAULT 'never', traffic_reset_day integer DEFAULT 1,
    created_at integer, updated_at integer, sync_orphaned_at integer DEFAULT 0
);
CREATE TABLE client_inbounds (
    client_id integer, inbound_id integer, flow_override text, created_at integer,
    PRIMARY KEY (client_id, inbound_id)
);
CREATE TABLE client_traffics (
    id integer PRIMARY KEY AUTOINCREMENT, inbound_id integer, enable numeric, email text UNIQUE,
    up integer, down integer, expiry_time integer, total integer, reset integer DEFAULT 0,
    reset_day integer DEFAULT 0, reset_max integer DEFAULT 0, reset_count integer DEFAULT 0,
    last_online integer DEFAULT 0, last_sub_fetch integer DEFAULT 0
);
"""


class RealityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reality-db-test-")
        self.root = Path(self.temp.name)
        self.db = self.root / "x-ui.db"
        self.output = self.root / "access.json"
        self.calls = self.root / "xray.calls"
        self.xray = self.root / "xray"
        self.xray.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"${XRAY_CALLS:?}\"\n"
            "printf 'PrivateKey: %s\\nPassword (PublicKey): %s\\nHash32: %s\\n' "
            f"'{PRIVATE}' '{PUBLIC}' '{'C' * 43}'\n",
            encoding="utf-8",
        )
        self.xray.chmod(0o755)
        with sqlite3.connect(self.db) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO users (id, username, password) VALUES (7, 'admin', 'hash')")
            conn.execute(
                "INSERT INTO inbounds (user_id,up,down,total,remark,enable,expiry_time,listen,port,"
                "protocol,settings,stream_settings,tag,sniffing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (7, 12, 34, 0, "existing", 1, 0, "127.0.0.1", 24444, "vless",
                 '{"clients":[]}', '{}', "existing-tag", '{"enabled":false}'),
            )

    def tearDown(self):
        self.temp.cleanup()

    def run_helper(self, domain="node.example.com", expect_ok=True):
        command = (
            "set -Eeuo pipefail; source \"$1\"; "
            "configure_reality_inbound \"$2\" \"$3\" \"$4\" \"$5\""
        )
        env = dict(os.environ, XRAY_CALLS=str(self.calls))
        proc = subprocess.run(
            ["bash", "-c", command, "reality-db-test", str(HELPER), str(self.db),
             domain, str(self.xray), str(self.output)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False,
        )
        if expect_ok and proc.returncode != 0:
            self.fail(f"helper failed ({proc.returncode}): {proc.stderr}\n{proc.stdout}")
        if not expect_ok and proc.returncode == 0:
            self.fail("helper unexpectedly succeeded")
        return proc

    def load_owned(self, conn):
        return conn.execute(
            "SELECT * FROM inbounds WHERE remark='REALITY-443-Caddy'"
        ).fetchone()

    def test_creates_complete_v38_identity_and_safe_output(self):
        self.run_helper()
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            inbound = self.load_owned(conn)
            self.assertIsNotNone(inbound)
            self.assertEqual((inbound["user_id"], inbound["listen"], inbound["port"], inbound["protocol"]),
                             (7, "0.0.0.0", 443, "vless"))
            self.assertEqual(conn.execute("SELECT count(*) FROM inbounds").fetchone()[0], 2)
            settings = json.loads(inbound["settings"])
            stream = json.loads(inbound["stream_settings"])
            client_json = settings["clients"][0]
            reality = stream["realitySettings"]
            self.assertEqual((stream["network"], stream["security"]), ("tcp", "reality"))
            self.assertEqual(reality["target"], "127.0.0.1:8443")
            self.assertEqual(reality["serverNames"], ["node.example.com"])
            self.assertEqual(reality["settings"]["publicKey"], PUBLIC)
            self.assertEqual(client_json["flow"], "xtls-rprx-vision")

            client = conn.execute("SELECT * FROM clients WHERE email=?", (client_json["email"],)).fetchone()
            link = conn.execute("SELECT * FROM client_inbounds WHERE inbound_id=?", (inbound["id"],)).fetchone()
            traffic = conn.execute("SELECT * FROM client_traffics WHERE inbound_id=?", (inbound["id"],)).fetchone()
            self.assertEqual(client["uuid"], client_json["id"])
            self.assertEqual(client["sub_id"], client_json["subId"])
            self.assertEqual(link["client_id"], client["id"])
            self.assertEqual(link["flow_override"], "xtls-rprx-vision")
            self.assertEqual((traffic["email"], traffic["up"], traffic["down"]),
                             (client["email"], 0, 0))

        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["public_key"], PUBLIC)
        self.assertEqual(result["server_name"], "node.example.com")
        self.assertEqual(result["port"], 443)
        self.assertIn(f"vless://{result['uuid']}@node.example.com:443?", result["vless_uri"])
        self.assertNotIn(PRIVATE, self.output.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(self.calls.read_text(encoding="utf-8").strip(), "x25519")

    def test_idempotent_repair_preserves_all_clients_and_statistics(self):
        self.run_helper("old.example.com")
        first = json.loads(self.output.read_text(encoding="utf-8"))
        with sqlite3.connect(self.db) as conn:
            inbound = conn.execute(
                "SELECT id, settings FROM inbounds WHERE remark='REALITY-443-Caddy'"
            ).fetchone()
            inbound_id, raw_settings = inbound
            second_uuid = "22222222-2222-4222-8222-222222222222"
            now = 1700000000000
            conn.execute(
                "INSERT INTO clients (email,sub_id,uuid,flow,enable,created_at,updated_at) "
                "VALUES ('second@example', 'second-sub', ?, 'xtls-rprx-vision', 1, ?, ?)",
                (second_uuid, now, now),
            )
            second_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO client_inbounds VALUES (?,?,?,?)",
                (second_id, inbound_id, "xtls-rprx-vision", now),
            )
            conn.execute(
                "INSERT INTO client_traffics (inbound_id,enable,email,up,down,expiry_time,total) "
                "VALUES (?,?,?,123,456,0,0)",
                (inbound_id, 1, "second@example"),
            )
            conn.execute(
                "UPDATE client_traffics SET up=987, down=654 WHERE inbound_id=? AND email=?",
                (inbound_id, first["email"]),
            )
            settings = json.loads(raw_settings)
            settings["clients"].append({
                "id": second_uuid, "email": "second@example", "subId": "second-sub",
                "flow": "xtls-rprx-vision", "enable": True,
            })
            conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), inbound_id))

        self.run_helper("new.example.com")
        second = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(second["uuid"], first["uuid"])
        self.assertEqual(second["sub_id"], first["sub_id"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM inbounds WHERE remark='REALITY-443-Caddy'"
            ).fetchone()[0], 1)
            inbound = conn.execute(
                "SELECT id,settings,stream_settings,up,down FROM inbounds WHERE remark='REALITY-443-Caddy'"
            ).fetchone()
            self.assertEqual(len(json.loads(inbound[1])["clients"]), 2)
            self.assertEqual(json.loads(inbound[2])["realitySettings"]["serverNames"], ["new.example.com"])
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM client_inbounds WHERE inbound_id=?", (inbound[0],)
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute(
                "SELECT up,down FROM client_traffics WHERE inbound_id=? AND email=?",
                (inbound[0], first["email"]),
            ).fetchone(), (987, 654))
            self.assertEqual(conn.execute(
                "SELECT up,down FROM client_traffics WHERE inbound_id=? AND email='second@example'",
                (inbound[0],),
            ).fetchone(), (123, 456))
        # Re-derive the public key from the same private key without rotating it.
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["x25519", "x25519 -i " + PRIVATE])

    def test_rejects_another_inbound_on_443_without_partial_writes(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE inbounds SET port=443 WHERE remark='existing'")
        proc = self.run_helper(expect_ok=False)
        self.assertIn("443", proc.stderr)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM inbounds WHERE remark='REALITY-443-Caddy'"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM clients").fetchone()[0], 0)
        self.assertFalse(self.output.exists())

    def test_rejects_malformed_same_name_without_overwrite(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE inbounds SET remark='REALITY-443-Caddy', protocol='vmess', port=10443 "
                "WHERE remark='existing'"
            )
        before = self.db.read_bytes()
        proc = self.run_helper(expect_ok=False)
        self.assertIn("VLESS", proc.stderr)
        self.assertEqual(self.db.read_bytes(), before)

    def test_rejects_uri_when_existing_client_no_longer_uses_vision(self):
        self.run_helper()
        original_output = self.output.read_bytes()
        with sqlite3.connect(self.db) as conn:
            inbound_id = conn.execute(
                "SELECT id FROM inbounds WHERE remark='REALITY-443-Caddy'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE client_inbounds SET flow_override='' WHERE inbound_id=?", (inbound_id,)
            )
        proc = self.run_helper("changed.example.com", expect_ok=False)
        self.assertIn("xtls-rprx-vision", proc.stderr)
        self.assertEqual(self.output.read_bytes(), original_output)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT flow_override FROM client_inbounds WHERE inbound_id=?", (inbound_id,)
            ).fetchone()[0], "")
            stream = json.loads(conn.execute(
                "SELECT stream_settings FROM inbounds WHERE id=?", (inbound_id,)
            ).fetchone()[0])
            self.assertEqual(stream["realitySettings"]["serverNames"], ["node.example.com"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
