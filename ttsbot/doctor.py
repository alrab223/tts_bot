"""Discordへのログイン・外部通信なしでOSとPythonの依存を確認する。"""

import ctypes.util
import json
import shutil
import subprocess
from importlib.metadata import version

import discord

from .config import Config


def check(config: Config) -> dict:
   executable = shutil.which(config.ffmpeg)
   if not executable:
      raise RuntimeError(
         "FFmpegが見つかりません。Dockerまたはaptでffmpegを導入してください。"
      )
   opus = ctypes.util.find_library("opus")
   if not opus:
      raise RuntimeError(
         "libopusが見つかりません。libopus0を導入してください。"
      )
   if not discord.opus.is_loaded():
      discord.opus.load_opus(opus)
   encoder = discord.opus.Encoder()
   packet = encoder.encode(
      bytes(encoder.FRAME_SIZE), encoder.SAMPLES_PER_FRAME
   )
   if not packet:
      raise RuntimeError("OpusによるPCMのエンコードに失敗しました。")
   import davey  # noqa: F401
   import nacl.bindings  # noqa: F401

   result = subprocess.run(
      [executable, "-version"],
      check=True,
      capture_output=True,
      text=True,
      timeout=10,
   )
   subprocess.run(
      [
         executable,
         "-v",
         "error",
         "-f",
         "lavfi",
         "-i",
         "anullsrc=r=48000:cl=stereo",
         "-t",
         "0.05",
         "-f",
         "s16le",
         "-",
      ],
      check=True,
      capture_output=True,
      timeout=10,
   )
   config.prepare()
   return {
      "ok": True,
      "discord.py": version("discord.py"),
      "davey": version("davey"),
      "PyNaCl": version("PyNaCl"),
      "ffmpeg": result.stdout.splitlines()[0],
      "opus": opus,
      "opus_packet_bytes": len(packet),
      "data_dir": str(config.data_dir),
   }


def main():
   print(json.dumps(check(Config.from_env()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
   main()
