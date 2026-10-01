import asyncio
from copy import deepcopy
from contextlib import closing
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "legacy").mkdir()
        (self.root / "identity-mappings.json").write_text(json.dumps({
            "legacy_aliases": {"Legacy Input A": "Alfa"},
            "manual_overrides": {}, "manual_aliases": {},
        }), encoding="utf-8")
        self.environment = patch.dict(os.environ, {"SCOREKEEPER_PRIVATE_DATA_DIR": str(self.root), "SERVER_ID": "123"})
        self.environment.start()
        import config, public_identity, public_projection
        self.config, self.identity, self.projection = config, public_identity, public_projection
        self.config.runtime_storage_paths.cache_clear()

    def tearDown(self):
        self.config.runtime_storage_paths.cache_clear()
        self.environment.stop()
        self.temp.cleanup()

    def source(self, score="10"):
        path = self.root / "legacy" / "2024_Split1.csv"
        path.write_text("PlayerID,Score,GameName,Date,Notes\n"
                        f"Legacy Input A,{score},Private Game,01/01/2024,Keep this fixture note private\n"
                        "Legacy Input A,2.25,Private Game,01/02/2024,Private note\n"
                        "Unlisted Input B,3,Private Game,01/03/2024,Private note\n", encoding="utf-8")
        return path

    def test_scores_are_preserved_and_only_approved_fields_export(self):
        source = self.source()
        self.assertEqual(self.projection.read_legacy_totals(source)["Legacy Input A"], Decimal("12.25"))
        value = self.projection.build_projection()
        rows = value["splits"][0]["players"]
        self.assertEqual(rows[0], {"rank": 1, "display_name": "Alfa", "total_score": 12.25})
        self.assertRegex(rows[1]["display_name"], r"^Player #\d+$")
        encoded = json.dumps(value)
        for private in ["Legacy Input", "Unlisted Input", "Private Game", "Private note", "01/01/2024", "Notes", "PlayerID"]:
            self.assertNotIn(private, encoded)

    def test_public_identity_is_stable_and_independent_of_discord_id(self):
        discord_id = int("9" * 18)
        first = self.identity.display_name_for_discord(discord_id)
        self.assertNotIn(str(discord_id), first)
        self.assertEqual(self.identity.display_name_for_discord(discord_id, "Chosen Alias"), "Chosen Alias")
        self.assertEqual(self.identity.display_name_for_discord(discord_id), first)
        result = subprocess.check_output([sys.executable, "-c", "from public_identity import display_name_for_discord; print(display_name_for_discord(int('9'*18)))"], cwd=Path(__file__).resolve().parents[1], text=True)
        self.assertEqual(result.strip(), first)
        self.assertNotEqual(self.identity.display_name_for_discord(discord_id - 1), first)

    def test_unsafe_alias_does_not_become_public_identity(self):
        for alias in [str(int("9" * 18)), "bad\nname", "Player #123"]:
            self.assertRegex(self.identity.display_name_for_discord(int("9" * 18), alias), r"^Player #\d+$")

    def test_private_fields_and_nonfinite_scores_are_rejected(self):
        self.source()
        value = self.projection.build_projection()
        leaked = deepcopy(value)
        leaked["splits"][0]["players"][0]["discord_id"] = "private"
        with self.assertRaises(ValueError): self.projection.validate_projection(leaked)
        for score in ["NaN", "Infinity", "not-a-score"]:
            self.source(score)
            with self.assertRaises(ValueError): self.projection.build_projection()

    def test_export_never_overwrites_private_storage(self):
        self.source()
        with self.assertRaises(ValueError): self.projection.write_projection(self.root / "identity-mappings.json")
        self.assertIn("legacy_aliases", (self.root / "identity-mappings.json").read_text())

    def test_missing_mapping_fails_closed(self):
        self.source()
        (self.root / "identity-mappings.json").unlink()
        with self.assertRaises(RuntimeError): self.projection.build_projection()

    def test_authoritative_website_scores_take_precedence_without_changing_bot_originals(self):
        original = self.source()
        prior = original.read_bytes()
        website = self.root / "website-legacy-originals"
        website.mkdir()
        (website / original.name).write_text("PlayerID,Score\nLegacy Input A,20\n")
        value = self.projection.build_projection()
        self.assertEqual(value["splits"][0]["players"][0]["total_score"], 20)
        self.assertEqual(original.read_bytes(), prior)

    def test_bot_leaderboard_does_not_disclose_attendance_or_ids(self):
        import db
        db.DB_PATH = str(self.root / "bot.db")
        db.init_db()
        import competitive_scoring
        row = {"discord_id": int("9" * 18), "total_points": 7, "hours": 123.456, "nights": 234, "adjusted_total": 999, "missing": "private eligibility"}
        embed = competitive_scoring.build_leaderboard_embed(None, "2024_Split1", [row], [], 10)
        value = json.dumps(embed.to_dict())
        for private in [str(row["discord_id"]), "123.456", "private eligibility", "hours", "nights", "Eligibility", "adjusted_total"]:
            self.assertNotIn(private, value)
        self.assertIn("Player #", value)
        self.assertIn("7 points", value)

    def test_legacy_writes_require_council_in_authorized_guild(self):
        import discord
        from discord.ext import commands
        import scoring
        bot = commands.Bot(command_prefix="/", intents=discord.Intents.none())
        scoring.register(bot)
        command = bot.tree.get_command("updatescore")
        for guild, roles in [(SimpleNamespace(id=123), []), (SimpleNamespace(id=456), [SimpleNamespace(name="Game Night Council")])]:
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(roles=roles), response=response)
            asyncio.run(command.callback(interaction, "Fixture", 1, "Fixture Game"))
            response.send_message.assert_awaited_once()
            self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])
        self.assertEqual(list((self.root / "legacy").glob("*.csv")), [])

    def test_member_channel_fails_closed_for_public_and_unknown_roles(self):
        from privacy import is_member_only_channel
        default = SimpleNamespace(name="everyone", permissions=SimpleNamespace(administrator=False))
        member = SimpleNamespace(name="Game Night", permissions=SimpleNamespace(administrator=False))
        unknown = SimpleNamespace(name="Unapproved", permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(id=123, default_role=default, roles=[default, member, unknown])
        visible = {id(member)}
        channel = SimpleNamespace(guild=guild, overwrites={}, permissions_for=lambda role: SimpleNamespace(view_channel=id(role) in visible))
        self.assertTrue(is_member_only_channel(channel))
        visible.add(id(default))
        self.assertFalse(is_member_only_channel(channel))
        visible.remove(id(default)); visible.add(id(unknown))
        self.assertFalse(is_member_only_channel(channel))
        visible.remove(id(unknown)); guild.id = 456
        self.assertFalse(is_member_only_channel(channel))

    def test_private_only_guard_blocks_pending_cutover_without_writing_database(self):
        with patch.dict(os.environ):
            os.environ.pop("SCOREKEEPER_PRIVATE_DATA_DIR", None)
            with patch.object(self.config, "_local_config", return_value={"private_data_dir": str(self.root), "storage_ready": False}):
                with self.assertRaisesRegex(RuntimeError, "cutover is pending"):
                    self.config.require_private_storage_ready()
        self.assertFalse((self.root / "bot.db").exists())

    def test_member_channel_rejects_direct_access_for_nonmember(self):
        from privacy import is_member_only_channel
        default = SimpleNamespace(name="everyone", permissions=SimpleNamespace(administrator=False))
        member = SimpleNamespace(name="Game Night", permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(id=123, default_role=default, roles=[default, member])
        guest = type("Guest", (), {"roles": []})()
        channel = SimpleNamespace(guild=guild, overwrites={guest: SimpleNamespace(view_channel=True)}, permissions_for=lambda role: SimpleNamespace(view_channel=role is member))
        self.assertFalse(is_member_only_channel(channel))
        guest.roles = [member]
        self.assertTrue(is_member_only_channel(channel))

    def test_full_command_registration_does_not_require_login(self):
        import db
        db.DB_PATH = str(self.root / "bot.db")
        from bot import create_bot
        bot = create_bot()
        names = {command.name for command in bot.tree.get_commands()}
        self.assertTrue({"leaderboard", "loggame", "stats", "mystats", "setalias", "schedulegamenight", "updatescore"}.issubset(names))
        self.assertIsNone(bot.user)

    def test_startup_reconciliation_uses_explicit_client_without_real_io(self):
        import rsvp
        import announcements
        from constants import ANNOUNCEMENT_CHANNEL_NAME
        async def history(**kwargs):
            yield SimpleNamespace(content="Fixture 01/01/2000", reactions=[])
        channel = SimpleNamespace(name=ANNOUNCEMENT_CHANNEL_NAME, history=history)
        guild = SimpleNamespace(text_channels=[channel])
        client = SimpleNamespace()
        with patch.object(rsvp, "sync_schedule_attendance_snapshot") as sync, patch.object(rsvp, "_remind_alias_if_missing", new_callable=AsyncMock) as remind, patch.object(announcements, "refresh_announcement", new_callable=AsyncMock) as refresh:
            asyncio.run(rsvp.reconcile_active_invitation(client, guild))
            sync.assert_called_once()
            remind.assert_not_awaited()
            refresh.assert_awaited_once_with(client, "2000-01-01")

    def test_pending_runtime_uses_originals_and_freezes_selection(self):
        original = self.root / "original"
        (original / "data/legacy").mkdir(parents=True)
        (original / "data/bot.db").write_bytes(b"original fixture")
        local = {"private_data_dir": str(self.root), "storage_ready": False, "legacy_runtime": True}
        with patch.dict(os.environ), patch.object(self.config, "PROJECT_ROOT", original), patch.object(self.config, "_local_config", return_value=local):
            os.environ.pop("SCOREKEEPER_PRIVATE_DATA_DIR", None)
            self.assertEqual(self.config.get_runtime_database_path(), original / "data/bot.db")
            self.assertEqual(self.config.get_runtime_schedules_dir(), original)
            self.assertEqual(self.config.get_runtime_legacy_dir(), original / "data/legacy")
            local["storage_ready"] = True
            self.assertEqual(self.config.get_runtime_database_path(), original / "data/bot.db")

    def test_pending_runtime_without_originals_fails_closed(self):
        local = {"private_data_dir": str(self.root), "storage_ready": False, "legacy_runtime": True}
        with patch.dict(os.environ), patch.object(self.config, "PROJECT_ROOT", self.root), patch.object(self.config, "_local_config", return_value=local):
            os.environ.pop("SCOREKEEPER_PRIVATE_DATA_DIR", None)
            with self.assertRaises(RuntimeError): self.config.get_runtime_database_path()
        self.assertFalse((self.root / "bot.db").exists())

    def test_cutover_refuses_running_bot_without_modifying_files(self):
        import migrate_private_storage as migration
        with patch.object(migration, "get_private_data_dir", return_value=self.root), patch.object(migration, "running_bot_count", return_value=1):
            before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
            with self.assertRaisesRegex(RuntimeError, "bot is running"): migration.apply_cutover()
            self.assertEqual(before, sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")))

    def test_cutover_preserves_latest_database_and_csvs(self):
        import migrate_private_storage as migration
        original = self.root / "original"
        private = self.root / "private"
        (original / "data/legacy").mkdir(parents=True)
        private.mkdir()
        with closing(sqlite3.connect(original / "data/bot.db")) as conn, conn:
            conn.execute("CREATE TABLE records(value TEXT)")
            conn.execute("INSERT INTO records VALUES ('latest private fixture')")
        with closing(sqlite3.connect(private / "bot.db")) as conn, conn:
            conn.execute("CREATE TABLE records(value TEXT)")
            conn.execute("INSERT INTO records VALUES ('stale fixture')")
        csv_source = original / "2026_Split2_gamenights.csv"
        csv_source.write_text("Date,Notes\nfixture,latest private fixture\n")
        (original / "data/legacy/2024_Split1.csv").write_text("PlayerID,Score\nfixture,4\n")
        (original / "pending_cutover").mkdir()
        (original / "announcements.py").write_text("VALUE = 'original'\n")
        (original / "pending_cutover/announcements.py").write_text("VALUE = 'replacement'\n")
        config = original / ".private-config.json"
        config.write_text(json.dumps({"private_data_dir": str(private), "storage_ready": False, "legacy_runtime": True}))
        with patch.object(migration, "get_private_data_dir", return_value=private), patch.object(migration, "PROJECT_ROOT", original), patch.object(migration, "LOCAL_CONFIG", config), patch.object(migration, "running_bot_count", return_value=0):
            migration.apply_cutover()
        with closing(sqlite3.connect(private / "bot.db")) as conn:
            self.assertEqual(conn.execute("SELECT value FROM records").fetchone()[0], "latest private fixture")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual((private / "schedules" / csv_source.name).read_bytes(), csv_source.read_bytes())
        self.assertTrue(json.loads(config.read_text())["storage_ready"])
        self.assertIn("replacement", (original / "announcements.py").read_text())
        backup = next((self.root / "backups").glob("cutover-*"))
        self.assertIn("original", (backup / "source/announcements.py").read_text())
        self.assertTrue((backup / "previous-private-bot.db").is_file())
        self.assertTrue((original / "data/bot.db").is_file())


if __name__ == "__main__":
    unittest.main()
