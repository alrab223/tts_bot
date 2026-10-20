"""音声合成と読み上げ用テキスト処理。同期I/OはCog側でワーカースレッドへ渡す。"""

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from gtts import gTTS

from .config import Config

LOGGER = logging.getLogger(__name__)
SOUND_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


def sanitize(text: str, attachments=False) -> str:
   text = re.sub(r"https?://\S+", "URL省略", text)
   if text.startswith(("<", "!")):
      return ""
   if text.count("\n") > 4:
      return "改行が多数検出されたため、省略します"
   if len(text) > 100:
      return "文字数が多いため省略します"
   return text.strip() or ("画像が添付されました" if attachments else "")


def normalize_dictionary(rows: list[list[str]]) -> list[tuple[str, str, int]]:
   rules = []
   for row in rows:
      if (
         len(row) < 2
         or not row[0].strip()
         or row[0].strip().lower() in {"word", "単語", "変換前"}
      ):
         continue
      try:
         priority = int(row[2]) if len(row) > 2 and row[2].strip() else 1
      except ValueError:
         continue
      rules.append((row[0], row[1], priority))
   return sorted(rules, key=lambda item: item[2])


def replace_words(text: str, rules: list[tuple[str, str, int]]) -> str:
   for word, reading, _ in rules:
      text = text.replace(word, reading)
   return text


def split_sounds(
   text: str, sounds: list[dict]
) -> list[tuple[str, dict | None]]:
   """文章の順番を保ち、最初に出現する短縮SEを優先度順に分離する。"""
   parts = []
   active = [
      sound for sound in sounds if sound["short"] == 1 and sound["word"]
   ]
   while text:
      found = [
         (text.find(sound["word"]), order, sound)
         for order, sound in enumerate(active)
         if sound["word"] in text
      ]
      if not found:
         parts.append((text, None))
         break
      index, _, sound = min(found, key=lambda item: (item[0], item[1]))
      if index:
         parts.append((text[:index], None))
      parts.append(("", sound))
      next_index = index + len(sound["word"])
      text = text[next_index:]
   return parts


@dataclass(frozen=True)
class Clip:
   path: Path
   volume: float = 0.5


class Synthesizer:
   def __init__(self, config: Config):
      self.config = config
      self.rules = []

   def load_dictionary(self) -> int:
      if self.config.sheet_id or self.config.sheet_name:
         import gspread

         client = gspread.service_account(
            filename=str(self.config.service_account)
         )
         book = (
            client.open_by_key(self.config.sheet_id)
            if self.config.sheet_id
            else client.open(self.config.sheet_name)
         )
         rows = book.sheet1.get_all_values()
      else:
         with (self.config.data_dir / "dictionary.csv").open(
            encoding="utf-8-sig", newline=""
         ) as stream:
            rows = list(csv.reader(stream))
      rules = normalize_dictionary(rows)
      self.rules = rules
      return len(rules)

   def audio_path(self, value: str) -> Path:
      path = (self.config.data_dir / value).resolve()
      if (
         not path.is_relative_to((self.config.data_dir / "audio").resolve())
         or not path.is_file()
      ):
         raise ValueError("SEがdata/audio内に見つかりません。")
      return path

   def render(
      self, text: str, sounds: list[dict], directory: Path
   ) -> list[Clip]:
      clips = []
      for index, (part, sound) in enumerate(split_sounds(text, sounds)):
         if sound:
            try:
               clips.append(
                  Clip(
                     self.audio_path(sound["sound_path"]),
                     float(sound["volume"]),
                  )
               )
            except ValueError:
               LOGGER.warning("A configured sound effect is missing")
         elif replace_words(part, self.rules).strip():
            path = directory / f"gtts_{index}.mp3"
            gTTS(
               text=replace_words(part, self.rules),
               lang="ja",
               timeout=(3, 15),
            ).save(str(path))
            clips.append(Clip(path))
      return clips
