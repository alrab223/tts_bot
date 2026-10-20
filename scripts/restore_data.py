"""停止中のnamed volumeへ読み上げデータを初回だけ復元する。"""

import argparse
import shutil
import sqlite3
from pathlib import Path


def restore(source: Path, target: Path):
   source, target = source.resolve(), target.resolve()
   if not (source / "speech.sqlite3").is_file():
      raise ValueError("移行元にspeech.sqlite3がありません。")
   target.mkdir(parents=True, exist_ok=True)
   if any(target.iterdir()):
      raise ValueError(
         "復元先が空ではありません。既存データの上書きは行いません。"
      )
   # キャッシュと一時ファイルは除外し、Bot停止後のDBを使う。
   with sqlite3.connect(
      (source / "speech.sqlite3").as_uri() + "?mode=ro", uri=True
   ) as original:
      with sqlite3.connect(target / "speech.sqlite3") as destination:
         original.backup(destination)
   for name in ("dictionary.csv",):
      if (source / name).exists():
         shutil.copy2(source / name, target / name)
   if (source / "audio").exists():
      shutil.copytree(source / "audio", target / "audio")


if __name__ == "__main__":
   parser = argparse.ArgumentParser(description=__doc__)
   parser.add_argument("--source", type=Path, default=Path("/import"))
   parser.add_argument("--target", type=Path, default=Path("/app/data"))
   args = parser.parse_args()
   restore(args.source, args.target)
   print("読み上げ設定と音声ファイルを復元しました。")
