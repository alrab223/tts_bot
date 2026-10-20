import argparse
import asyncio
import logging
import os
import signal
import time

import discord
from discord.ext import commands
from dotenv import load_dotenv

from ttsbot.config import PROJECT_ROOT, Config
from ttsbot.doctor import check
from ttsbot.store import Store
from ttsbot.synthesis import Synthesizer

LOGGER = logging.getLogger(__name__)


class SpeechBot(commands.Bot):
   def __init__(self, config: Config):
      intents = discord.Intents.default()
      intents.message_content = True
      intents.voice_states = True
      super().__init__(
         command_prefix="!",
         intents=intents,
         allowed_mentions=discord.AllowedMentions.none(),
      )
      self.config = config
      config.prepare()
      self.store = Store(config.data_dir / "speech.sqlite3")
      self.synth = Synthesizer(config)
      self.heartbeat_task = None
      self._speech_closed = False

   async def setup_hook(self):
      try:
         await asyncio.to_thread(self.synth.load_dictionary)
      except Exception:
         LOGGER.warning(
            "Dictionary unavailable; continuing without replacements"
         )
      await self.load_extension("ttsbot.cogs.speech")
      if self.config.command_guild_id:
         guild = discord.Object(id=self.config.command_guild_id)
         self.tree.copy_global_to(guild=guild)
         await self.tree.sync(guild=guild)
      else:
         await self.tree.sync()
      self.heartbeat_task = asyncio.create_task(self._heartbeat())

   async def _heartbeat(self):
      while True:
         if self.is_ready():
            (self.config.data_dir / "heartbeat").write_text(str(time.time()))
         await asyncio.sleep(10)

   async def close(self):
      if self._speech_closed:
         return
      self._speech_closed = True
      try:
         if self.heartbeat_task:
            self.heartbeat_task.cancel()
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
         (self.config.data_dir / "heartbeat").unlink(missing_ok=True)
         cog = self.get_cog("Speech")
         if cog:
            await cog.shutdown()
      finally:
         self.store.close()
         await super().close()


async def run(config: Config, token: str):
   async with SpeechBot(config) as bot:
      loop = asyncio.get_running_loop()
      for sig in (signal.SIGTERM, signal.SIGINT):
         try:
            loop.add_signal_handler(
               sig, lambda: asyncio.create_task(bot.close())
            )
         except NotImplementedError:
            pass
      await bot.start(token)


def main():
   load_dotenv(PROJECT_ROOT / ".env")
   parser = argparse.ArgumentParser(description="Discord読み上げ専用Bot")
   parser.add_argument(
      "--check",
      action="store_true",
      help="ログインせずネイティブ依存を検証する",
   )
   args = parser.parse_args()
   discord.utils.setup_logging()
   try:
      config = Config.from_env()
      report = check(config)
      if args.check:
         import json

         print(json.dumps(report, ensure_ascii=False, indent=2))
         return
      token = os.getenv("BOT_TOKEN", "").strip()
      if not token:
         raise ValueError(
            "BOT_TOKENが未設定です。.env.exampleを.envにコピーして設定してください。"
         )
      asyncio.run(run(config, token))
   except KeyboardInterrupt:
      pass
   except Exception as error:
      # トークン・読み上げ本文・Google認証情報をログへ含めない。
      LOGGER.error("Bot startup failed (%s)", type(error).__name__)
      if isinstance(error, (ValueError, RuntimeError)):
         LOGGER.error("%s", error)
      raise SystemExit(1) from None


if __name__ == "__main__":
   main()
