import sqlite3
from pathlib import Path


class Store:
   def __init__(self, path: Path):
      path.parent.mkdir(parents=True, exist_ok=True)
      self.connection = sqlite3.connect(path, timeout=10)
      self.connection.row_factory = sqlite3.Row
      self.connection.execute("PRAGMA journal_mode=WAL")
      self.connection.execute("PRAGMA busy_timeout=5000")
      self.connection.executescript("""
         CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            read_enabled INTEGER NOT NULL DEFAULT 1,
            read_channel_id INTEGER, voice_channel_id INTEGER
         );
         CREATE TABLE IF NOT EXISTS read_text_se (
            word TEXT PRIMARY KEY, sound_path TEXT NOT NULL,
            volume REAL NOT NULL DEFAULT 0.1,
            short INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 1
         );
         CREATE TABLE IF NOT EXISTS enter_se (
            id INTEGER PRIMARY KEY, name TEXT, se TEXT NOT NULL,
            volume REAL NOT NULL DEFAULT 0.3
         );
      """)
      self._remove_legacy_voice_settings()

   def _remove_legacy_voice_settings(self):
      """既存の移行先DBから、廃止した合成エンジンの設定だけを除去する。"""
      columns = {
         row[1]
         for row in self.connection.execute(
            "PRAGMA table_info(guild_settings)"
         )
      }
      with self.connection:
         self.connection.execute("BEGIN IMMEDIATE")
         if "vits_enabled" in columns:
            # 古いSQLiteでも使えるよう、テーブルを再作成する。
            self.connection.execute(
               "ALTER TABLE guild_settings RENAME TO guild_settings_old"
            )
            self.connection.execute("""
               CREATE TABLE guild_settings (
                  guild_id INTEGER PRIMARY KEY,
                  read_enabled INTEGER NOT NULL DEFAULT 1,
                  read_channel_id INTEGER, voice_channel_id INTEGER
               )
            """)
            self.connection.execute("""
               INSERT INTO guild_settings
               SELECT guild_id, read_enabled, read_channel_id, voice_channel_id
               FROM guild_settings_old
            """)
            self.connection.execute("DROP TABLE guild_settings_old")
         self.connection.execute("DROP TABLE IF EXISTS voice_profiles")

   def close(self):
      self.connection.close()

   def settings(self, guild_id: int) -> dict:
      with self.connection:
         self.connection.execute(
            "INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)",
            (guild_id,),
         )
      return dict(
         self.connection.execute(
            "SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,)
         ).fetchone()
      )

   def update_settings(self, guild_id: int, **values):
      allowed = {"read_enabled", "read_channel_id", "voice_channel_id"}
      if not values or not set(values) <= allowed:
         raise ValueError("Unknown setting")
      self.settings(guild_id)
      columns = ", ".join(f"{key}=?" for key in values)
      with self.connection:
         self.connection.execute(
            f"UPDATE guild_settings SET {columns} WHERE guild_id=?",
            (*values.values(), guild_id),
         )

   def connected_guilds(self) -> list[dict]:
      return [
         dict(row)
         for row in self.connection.execute(
            "SELECT * FROM guild_settings WHERE voice_channel_id IS NOT NULL"
         )
      ]

   def sounds(self) -> list[dict]:
      rows = self.connection.execute(
         "SELECT * FROM read_text_se ORDER BY priority ASC, rowid ASC"
      )
      return [dict(row) for row in rows]

   def add_sound(
      self, word: str, path: str, volume: float, short: int, priority: int
   ):
      with self.connection:
         self.connection.execute(
            "INSERT OR REPLACE INTO read_text_se VALUES (?, ?, ?, ?, ?)",
            (word, path, volume, short, priority),
         )

   def entrance(self, user_id: int) -> dict | None:
      row = self.connection.execute(
         "SELECT * FROM enter_se WHERE id=?", (user_id,)
      ).fetchone()
      return dict(row) if row else None
