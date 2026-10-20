"""元Botから読み上げ用の設定・参照音声だけを複製する。元のファイルには書き込まない。"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def migrate(
   source: Path,
   destination: Path,
   *,
   merge=False,
   guild_id: int | None = None,
   keep_channels=False,
) -> dict:
   from ttsbot.config import Config
   from ttsbot.store import Store

   source = source.resolve(strict=True)
   destination = destination.resolve()
   database = source / "data" / "bot.sqlite3"
   if not database.is_file():
      raise ValueError("元Botのdata/bot.sqlite3が見つかりません。")
   config = Config(destination)
   config.prepare()
   store = Store(destination / "speech.sqlite3")
   counts = {"sounds": 0, "entrances": 0, "missing_audio": 0}

   def copy_audio(value: str) -> str | None:
      path = (source / value).resolve()
      if not path.is_relative_to(source) or not path.is_file():
         counts["missing_audio"] += 1
         return None
      # 元の絶対パスやホスト固有の場所を移行後のDBへ残さない。
      digest = hashlib.sha256(path.read_bytes()).hexdigest()[:24]
      relative = f"audio/import_{digest}{path.suffix.lower()}"
      shutil.copy2(path, destination / relative)
      return relative

   try:
      populated = any(
         store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
            0
         ]
         for table in ("guild_settings", "read_text_se", "enter_se")
      )
      if populated and not merge:
         raise ValueError(
            "移行先に設定があります。別の空ディレクトリ、または--mergeを指定してください。"
         )
      with sqlite3.connect(
         database.as_uri() + "?mode=ro", uri=True
      ) as original:
         original.row_factory = sqlite3.Row
         original.execute("BEGIN")
         tables = {
            row[0]
            for row in original.execute(
               "SELECT name FROM sqlite_master WHERE type='table'"
            )
         }
         if "read_text_se" in tables:
            seen_words = set()
            for row in original.execute(
               "SELECT * FROM read_text_se ORDER BY priority, rowid"
            ):
               if row["word"] in seen_words:
                  continue
               audio = copy_audio(row["sound_path"])
               if audio:
                  seen_words.add(row["word"])
                  store.add_sound(
                     row["word"],
                     audio,
                     float(row["volume"])
                     if row["volume"] is not None
                     else 0.1,
                     int(row["short"]) if row["short"] is not None else 1,
                     int(row["priority"])
                     if row["priority"] is not None
                     else 1,
                  )
                  counts["sounds"] += 1
         if "enter_se" in tables:
            seen_users = set()
            for row in original.execute(
               "SELECT * FROM enter_se "
               "WHERE se IS NOT NULL AND se <> '' ORDER BY rowid"
            ):
               # 元Botと同じく先頭行を使い、重複行では上書きしない。
               if row["id"] in seen_users:
                  continue
               audio = copy_audio(row["se"])
               if audio:
                  seen_users.add(row["id"])
                  with store.connection:
                     store.connection.execute(
                        "INSERT OR REPLACE INTO enter_se VALUES (?, ?, ?, ?)",
                        (
                           row["id"],
                           row["name"],
                           audio,
                           float(row["volume"])
                           if row["volume"] is not None
                           else 0.3,
                        ),
                     )
                  counts["entrances"] += 1
         if guild_id:
            values = {}
            if "flag_control" in tables:
               row = original.execute(
                  "SELECT flag FROM flag_control WHERE flag_name='read_text'"
               ).fetchone()
               values["read_enabled"] = int(row[0]) if row else 1
            if keep_channels and "channel_flag_control" in tables:
               row = original.execute(
                  "SELECT channel_id FROM channel_flag_control "
                  "WHERE flag_name='read_text'"
               ).fetchone()
               if row:
                  values["read_channel_id"] = row[0]
            if values:
               store.update_settings(guild_id, **values)
      return counts
   finally:
      store.close()


def main():
   parser = argparse.ArgumentParser(description=__doc__)
   parser.add_argument(
      "--source", required=True, type=Path, help="元Botのルートフォルダ"
   )
   parser.add_argument(
      "--destination",
      type=Path,
      default=Path(__file__).resolve().parents[1] / "data",
   )
   parser.add_argument(
      "--merge",
      action="store_true",
      help="同じ単語・ユーザーIDの設定を更新する",
   )
   parser.add_argument(
      "--guild-id",
      type=int,
      help="読み上げオンオフの移行先DiscordサーバーID",
   )
   parser.add_argument(
      "--keep-channels",
      action="store_true",
      help="同一Discordサーバーへの移行時だけ旧読み上げ先を保持",
   )
   args = parser.parse_args()
   if args.keep_channels and not args.guild_id:
      parser.error("--keep-channelsには--guild-idも必要です。")
   print(
      json.dumps(
         migrate(
            args.source,
            args.destination,
            merge=args.merge,
            guild_id=args.guild_id,
            keep_channels=args.keep_channels,
         ),
         ensure_ascii=False,
         indent=2,
      )
   )
   print(
      "元Botの.env・Google認証情報・リマインダー・画像編集台帳・生成済み読み上げ音声はコピーしていません。"
   )


if __name__ == "__main__":
   main()
