import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import requests

from main import SpeechBot
from scripts.migrate_legacy import migrate
from scripts.restore_data import restore
from ttsbot.config import Config
from ttsbot.store import Store
from ttsbot.synthesis import (
   Clip,
   Synthesizer,
   normalize_dictionary,
   replace_words,
   sanitize,
   split_sounds,
)


class DataTests(unittest.TestCase):
   def setUp(self):
      self.directory = tempfile.TemporaryDirectory()
      self.root = Path(self.directory.name)
      self.config = Config(self.root / "data")
      self.config.prepare()
      self.store = Store(self.config.data_dir / "speech.sqlite3")

   def tearDown(self):
      self.store.close()
      self.directory.cleanup()

   def test_empty_project_initializes_without_credentials(self):
      self.assertTrue((self.config.data_dir / "dictionary.csv").exists())
      self.assertEqual(Synthesizer(self.config).load_dictionary(), 0)
      self.assertEqual(self.store.settings(1)["read_enabled"], 1)

   def test_settings_persist(self):
      self.store.update_settings(1, read_channel_id=100, voice_channel_id=200)
      self.store.update_settings(2, read_enabled=0, read_channel_id=101)
      reopened = Store(self.config.data_dir / "speech.sqlite3")
      try:
         self.assertEqual(reopened.settings(1)["read_channel_id"], 100)
         self.assertEqual(reopened.settings(2)["read_enabled"], 0)
         self.assertEqual(reopened.settings(1)["voice_channel_id"], 200)
      finally:
         reopened.close()

   def test_removed_engine_configuration_is_ignored(self):
      with patch.dict(
         "os.environ",
         {"VITS2_URL": "invalid", "VITS2_SERVER": "invalid"},
         clear=True,
      ):
         self.assertFalse(hasattr(Config.from_env(), "vits_url"))

   def test_empty_configuration_does_not_require_server(self):
      with patch.dict("os.environ", {}, clear=True):
         self.assertEqual(Config.from_env().queue_limit, 32)

   def test_existing_database_loses_only_removed_voice_settings(self):
      self.store.update_settings(1, read_channel_id=100, voice_channel_id=200)
      self.store.add_sound("SE", "audio/test.mp3", 0.2, 1, 3)
      with self.store.connection:
         self.store.connection.execute(
            "ALTER TABLE guild_settings "
            "ADD COLUMN vits_enabled INTEGER DEFAULT 1"
         )
         self.store.connection.execute(
            "CREATE TABLE voice_profiles"
            "(user_id, model_id, speaker_id, language, style)"
         )
         self.store.connection.execute(
            "INSERT INTO voice_profiles VALUES (5, 1, 2, 'JP', 'Happy')"
         )
      reopened = Store(self.config.data_dir / "speech.sqlite3")
      try:
         self.assertEqual(
            reopened.settings(1),
            {
               "guild_id": 1,
               "read_enabled": 1,
               "read_channel_id": 100,
               "voice_channel_id": 200,
            },
         )
         tables = {
            row[0]
            for row in reopened.connection.execute(
               "SELECT name FROM sqlite_master WHERE type='table'"
            )
         }
         self.assertNotIn("voice_profiles", tables)
         self.assertEqual(reopened.sounds()[0]["word"], "SE")
         with self.assertRaises(ValueError):
            reopened.update_settings(1, vits_enabled=1)
      finally:
         reopened.close()

   def test_replacements_are_ordered_numerically(self):
      rules = normalize_dictionary(
         [
            ["word", "reading", "priority"],
            ["abc", "x", "10"],
            ["abcd", "y", "2"],
         ]
      )
      self.assertEqual(replace_words("abcd", rules), "y")

   def test_message_sanitization(self):
      self.assertEqual(sanitize("https://example.test/image?a=1"), "URL省略")
      self.assertEqual(sanitize("", True), "画像が添付されました")
      self.assertEqual(sanitize("!ignored"), "")
      self.assertIn("省略", sanitize("あ" * 101))
      self.assertIn("改行", sanitize("a\n" * 5))

   def test_se_order_and_short_flag(self):
      sounds = [{"word": "SE", "short": 1}, {"word": "off", "short": 0}]
      self.assertEqual(
         split_sounds("前SE後SEoff", sounds),
         [
            ("前", None),
            ("", sounds[0]),
            ("後", None),
            ("", sounds[0]),
            ("off", None),
         ],
      )

   def test_audio_paths_cannot_escape_audio_directory(self):
      synth = Synthesizer(self.config)
      with self.assertRaises(ValueError):
         synth.audio_path("../secret.mp3")

   def test_gtts_synthesis_applies_dictionary(self):
      synth = Synthesizer(self.config)
      synth.rules = [("test", "テスト", 1)]

      def save(path):
         Path(path).write_bytes(b"fake mp3")

      with patch("ttsbot.synthesis.gTTS") as gtts:
         gtts.return_value.save.side_effect = save
         result = synth.render("test", [], self.root)
      self.assertEqual(len(result), 1)
      self.assertEqual(result[0].path.suffix, ".mp3")
      gtts.assert_called_once_with(text="テスト", lang="ja", timeout=(3, 15))

   def test_gtts_and_se_preserve_playback_order(self):
      synth = Synthesizer(self.config)
      audio = self.config.data_dir / "audio" / "test.ogg"
      audio.write_bytes(b"test audio")
      sound = {
         "word": "SE",
         "sound_path": "audio/test.ogg",
         "volume": 0.2,
         "short": 1,
      }
      with patch("ttsbot.synthesis.gTTS") as gtts:
         result = synth.render("前SE後", [sound], self.root)
      self.assertEqual(
         [clip.path.name for clip in result],
         ["gtts_0.mp3", "test.ogg", "gtts_2.mp3"],
      )
      self.assertEqual(result[1].volume, 0.2)
      self.assertEqual(
         [call.kwargs["text"] for call in gtts.call_args_list], ["前", "後"]
      )

   def test_migration_copies_only_speech_data(self):
      source = self.root / "legacy"
      (source / "data").mkdir(parents=True)
      (source / "music" / "se").mkdir(parents=True)
      (source / "json").mkdir()
      (source / "music" / "se" / "clip.mp3").write_bytes(b"test audio")
      (source / ".env").write_text("BOT_TOKEN=do-not-copy")
      (source / "json" / "service_account.json").write_text(
         '{"private_key":"do-not-copy"}'
      )
      (source / "json" / "user_vcmodel.json").write_text(
         json.dumps(
            {
               "123": {
                  "model_id": 2,
                  "speaker_id": 4,
                  "language": "JP",
                  "style": "Happy",
               },
            }
         )
      )
      database = source / "data" / "bot.sqlite3"
      with sqlite3.connect(database) as original:
         original.executescript("""
            CREATE TABLE read_text_se(
                word, sound_path, volume, short, priority
            );
            INSERT INTO read_text_se VALUES (
                'test', 'music/se/clip.mp3', 0.0, 1, 0
            );
            CREATE TABLE enter_se(id, name, se, volume);
            INSERT INTO enter_se VALUES (
                123, 'user', 'music/se/clip.mp3', 0.3
            );
            INSERT INTO enter_se VALUES (
                123, 'duplicate', 'music/se/clip.mp3', 0.9
            );
            CREATE TABLE reminders(id, reminder_text);
            INSERT INTO reminders VALUES (1, 'do-not-copy');
         """)
      before = database.read_bytes()
      destination = self.root / "imported"
      counts = migrate(source, destination)
      self.assertEqual(
         counts, {"sounds": 1, "entrances": 1, "missing_audio": 0}
      )
      self.assertEqual(database.read_bytes(), before)
      imported = Store(destination / "speech.sqlite3")
      try:
         self.assertEqual(imported.entrance(123)["volume"], 0.3)
         self.assertTrue(
            (destination / imported.sounds()[0]["sound_path"]).is_file()
         )
         self.assertEqual(imported.sounds()[0]["volume"], 0.0)
         self.assertEqual(imported.sounds()[0]["priority"], 0)
         tables = {
            row[0]
            for row in imported.connection.execute(
               "SELECT name FROM sqlite_master WHERE type='table'"
            )
         }
         self.assertNotIn("reminders", tables)
         self.assertNotIn("voice_profiles", tables)
         self.assertEqual(imported.connected_guilds(), [])
      finally:
         imported.close()
      self.assertFalse((destination / ".env").exists())
      self.assertFalse((destination / "json").exists())
      with self.assertRaises(ValueError):
         migrate(source, destination)

   def test_restore_refuses_to_overwrite_existing_data(self):
      source = self.root / "backup"
      source.mkdir()
      with sqlite3.connect(source / "speech.sqlite3") as database:
         database.execute("CREATE TABLE marker(value)")
         database.execute("INSERT INTO marker VALUES (1)")
      target = self.root / "volume"
      restore(source, target)
      with sqlite3.connect(target / "speech.sqlite3") as database:
         self.assertEqual(
            database.execute("SELECT value FROM marker").fetchone()[0], 1
         )
      with self.assertRaises(ValueError):
         restore(source, target)


class CogTests(unittest.IsolatedAsyncioTestCase):
   async def asyncSetUp(self):
      self.directory = tempfile.TemporaryDirectory()
      self.config = Config(Path(self.directory.name))
      self.bot = SpeechBot(self.config)
      await self.bot.load_extension("ttsbot.cogs.speech")
      self.cog = self.bot.get_cog("Speech")
      self.cog.voice = Mock(
         return_value=SimpleNamespace(
            is_connected=lambda: True, is_playing=lambda: False
         )
      )
      self.cog._play = AsyncMock()

   async def asyncTearDown(self):
      await self.bot.close()
      self.directory.cleanup()

   async def test_only_speech_commands_are_registered(self):
      names = {command.name for command in self.bot.tree.get_commands()}
      self.assertEqual(
         names,
         {
            "botをボイスチャンネルに召喚",
            "botをボイスチャットから追い出す",
            "読み上げ機能のオンオフ",
            "読み上げ停止",
            "朗読",
            "読み上げ辞書更新",
            "se追加",
         },
      )

   async def test_queued_messages_keep_order_per_guild(self):
      def render(text, sounds, directory):
         return [Clip(directory / (text + ".mp3"))]

      self.bot.synth.render = Mock(side_effect=render)
      self.cog.enqueue(1, "first")
      self.cog.enqueue(1, "second")
      await asyncio.wait_for(self.cog.session(1).queue.join(), 2)
      self.assertEqual(
         [call.args[1].path.name for call in self.cog._play.call_args_list],
         ["first.mp3", "second.mp3"],
      )
      self.assertEqual(list((self.config.data_dir / "cache").iterdir()), [])

   async def test_stopping_one_guild_does_not_stop_another(self):
      self.bot.synth.render = Mock(return_value=[])
      first = self.cog.session(1)
      other = self.cog.session(2)
      first.queue.put_nowait(("one", None, 0))
      other.queue.put_nowait(("two", None, 0))
      self.cog.stop(1)
      self.assertEqual(first.queue.qsize(), 0)
      self.assertEqual(other.queue.qsize(), 1)
      self.assertEqual(other.generation, 0)

   async def test_stop_during_synthesis_does_not_play_stale_audio(self):
      entered, resume = threading.Event(), threading.Event()

      def render(text, sounds, directory):
         entered.set()
         resume.wait(2)
         return [Clip(directory / "stale.mp3")]

      self.bot.synth.render = Mock(side_effect=render)
      self.cog.enqueue(1, "text")
      try:
         self.assertTrue(await asyncio.to_thread(entered.wait, 1))
         self.cog.stop(1)
      finally:
         resume.set()
      await asyncio.wait_for(self.cog.session(1).queue.join(), 2)
      self.cog._play.assert_not_awaited()

   async def test_synthesis_error_does_not_kill_the_queue(self):
      self.bot.synth.render = Mock(
         side_effect=[requests.ConnectionError(), []]
      )
      self.cog.enqueue(1, "bad")
      self.cog.enqueue(1, "good")
      await asyncio.wait_for(self.cog.session(1).queue.join(), 2)
      self.assertEqual(self.bot.synth.render.call_count, 2)
      self.assertEqual(list((self.config.data_dir / "cache").iterdir()), [])

   async def test_message_from_other_channel_is_not_read(self):
      self.bot.store.update_settings(1, read_channel_id=100)
      self.cog.enqueue = Mock()
      message = SimpleNamespace(
         author=SimpleNamespace(id=5, bot=False),
         guild=SimpleNamespace(id=1),
         channel=SimpleNamespace(id=101),
         clean_content="do not read",
         attachments=[],
      )
      await self.cog.on_message(message)
      self.cog.enqueue.assert_not_called()
      message.channel.id = 100
      await self.cog.on_message(message)
      self.cog.enqueue.assert_called_once_with(1, "do not read")

   async def test_queue_limit_is_enforced(self):
      state = self.cog.session(1)
      for _ in range(self.config.queue_limit):
         state.queue.put_nowait(("text", None, 0))
      with self.assertRaisesRegex(ValueError, "満杯"):
         self.cog.enqueue(1, "overflow")


if __name__ == "__main__":
   unittest.main()
