"""VC・キュー・読み上げ設定をサーバー単位で管理するCog。"""

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

from ..synthesis import SOUND_EXTENSIONS, Clip, sanitize

LOGGER = logging.getLogger(__name__)


@dataclass
class Session:
   queue: asyncio.Queue
   worker: asyncio.Task | None = None
   generation: int = 0
   manual_disconnect: bool = False
   connection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
   reconnect_failures: int = 0
   last_reconnect: float = 0


class Speech(commands.Cog):
   def __init__(self, bot):
      self.bot = bot
      self.store = bot.store
      self.synth = bot.synth
      self.config = bot.config
      self.sessions: dict[int, Session] = {}
      self.closing = False

   def session(self, guild_id: int) -> Session:
      if guild_id not in self.sessions:
         self.sessions[guild_id] = Session(
            asyncio.Queue(maxsize=self.config.queue_limit)
         )
      return self.sessions[guild_id]

   async def interaction_check(self, interaction: discord.Interaction) -> bool:
      if interaction.guild is None:
         await interaction.response.send_message(
            "サーバー内で実行してください。", ephemeral=True
         )
         return False
      return True

   def voice(self, guild_id: int):
      guild = self.bot.get_guild(guild_id)
      return guild.voice_client if guild else None

   def enqueue(self, guild_id: int, text: str, clip: Clip | None = None):
      voice = self.voice(guild_id)
      if self.closing or voice is None or not voice.is_connected():
         raise ValueError(
            "先に `/botをボイスチャンネルに召喚` でBotを参加させてください。"
         )
      session = self.session(guild_id)
      try:
         session.queue.put_nowait((text, clip, session.generation))
      except asyncio.QueueFull:
         raise ValueError(
            "読み上げ待ちが満杯です。少し待ってから実行してください。"
         ) from None
      if session.worker is None or session.worker.done():
         session.worker = asyncio.create_task(
            self._worker(guild_id), name=f"tts:{guild_id}"
         )

   def stop(self, guild_id: int):
      session = self.session(guild_id)
      session.generation += 1
      while not session.queue.empty():
         session.queue.get_nowait()
         session.queue.task_done()
      voice = self.voice(guild_id)
      if voice and voice.is_playing():
         voice.stop()

   async def _play(self, guild_id: int, clip: Clip, generation: int):
      voice = self.voice(guild_id)
      if (
         not voice
         or not voice.is_connected()
         or self.session(guild_id).generation != generation
      ):
         return
      loop = asyncio.get_running_loop()
      done = loop.create_future()

      def finish(error):
         def resolve():
            if not done.done():
               done.set_exception(error) if error else done.set_result(None)

         loop.call_soon_threadsafe(resolve)

      source = discord.PCMVolumeTransformer(
         discord.FFmpegPCMAudio(
            str(clip.path),
            executable=self.config.ffmpeg,
            before_options="-nostdin",
            options="-vn",
         ),
         volume=clip.volume,
      )
      try:
         voice.play(source, after=finish)
      except Exception:
         source.cleanup()
         raise
      try:
         await done
      except asyncio.CancelledError:
         voice.stop()
         raise

   async def _worker(self, guild_id: int):
      session = self.session(guild_id)
      while True:
         text, clip, generation = await session.queue.get()
         try:
            if generation != session.generation:
               continue
            if clip:
               await self._play(guild_id, clip, generation)
               continue
            with tempfile.TemporaryDirectory(
               prefix="tts_", dir=self.config.data_dir / "cache"
            ) as directory:
               render = asyncio.create_task(
                  asyncio.to_thread(
                     self.synth.render,
                     text,
                     self.store.sounds(),
                     Path(directory),
                  )
               )
               try:
                  clips = await asyncio.shield(render)
               except asyncio.CancelledError:
                  # 合成の終了を待って一時ファイルを片付ける。
                  try:
                     await render
                  except Exception:
                     pass
                  raise
               for result in clips:
                  if generation != session.generation:
                     break
                  await self._play(guild_id, result, generation)
         except asyncio.CancelledError:
            raise
         except Exception as error:
            LOGGER.warning(
               "Speech failed in guild %s (%s)",
               guild_id,
               type(error).__name__,
            )
         finally:
            session.queue.task_done()

   async def shutdown(self):
      self.closing = True
      for guild_id, session in self.sessions.items():
         self.stop(guild_id)
         session.manual_disconnect = True
         if session.worker:
            session.worker.cancel()
      await asyncio.gather(
         *(
            session.worker
            for session in self.sessions.values()
            if session.worker
         ),
         return_exceptions=True,
      )
      for voice in list(self.bot.voice_clients):
         try:
            await voice.disconnect(force=True)
         except Exception:
            LOGGER.warning("Voice disconnect failed during shutdown")

   @app_commands.command(
      name="botをボイスチャンネルに召喚",
      description="参加中のVCに接続し、このチャンネルの読み上げを開始します",
   )
   @app_commands.guild_only()
   async def connect(self, interaction: discord.Interaction):
      state = getattr(interaction.user, "voice", None)
      if not state or not isinstance(state.channel, discord.VoiceChannel):
         await interaction.response.send_message(
            "先にボイスチャンネルに参加してください。", ephemeral=True
         )
         return
      await interaction.response.defer(ephemeral=True, thinking=True)
      session = self.session(interaction.guild_id)
      async with session.connection_lock:
         session.manual_disconnect = False
         session.reconnect_failures = 0
         voice = self.voice(interaction.guild_id)
         try:
            if voice and voice.is_connected():
               self.stop(interaction.guild_id)
               if voice.channel.id != state.channel.id:
                  await voice.move_to(state.channel)
            else:
               await state.channel.connect(timeout=20, reconnect=True)
         except (discord.DiscordException, OSError, asyncio.TimeoutError):
            await interaction.edit_original_response(
               content="VCに接続できません。Botの接続・発言権限を確認してください。"
            )
            return
         self.store.update_settings(
            interaction.guild_id,
            voice_channel_id=state.channel.id,
            read_channel_id=interaction.channel_id,
            read_enabled=1,
         )
      await interaction.edit_original_response(
         content="VCに接続しました。このチャンネルを読み上げます。"
      )

   @app_commands.command(
      name="botをボイスチャットから追い出す",
      description="読み上げ待ちを破棄し、VCから退出します",
   )
   @app_commands.guild_only()
   async def disconnect(self, interaction: discord.Interaction):
      await interaction.response.defer(ephemeral=True, thinking=True)
      session = self.session(interaction.guild_id)
      session.manual_disconnect = True
      self.stop(interaction.guild_id)
      self.store.update_settings(interaction.guild_id, voice_channel_id=None)
      async with session.connection_lock:
         voice = self.voice(interaction.guild_id)
         if voice:
            await voice.disconnect(force=True)
      await interaction.edit_original_response(content="VCから退出しました。")

   @app_commands.command(
      name="読み上げ機能のオンオフ",
      description="このサーバーの読み上げを切り替えます",
   )
   @app_commands.guild_only()
   async def toggle_reading(self, interaction: discord.Interaction):
      enabled = not self.store.settings(interaction.guild_id)["read_enabled"]
      self.store.update_settings(
         interaction.guild_id,
         read_enabled=int(enabled),
         read_channel_id=interaction.channel_id if enabled else None,
      )
      if not enabled:
         self.stop(interaction.guild_id)
      await interaction.response.send_message(
         "読み上げをオンにしました。"
         if enabled
         else "読み上げをオフにしました。"
      )

   @app_commands.command(
      name="読み上げ停止",
      description="再生中の音声と、このサーバーの読み上げ待ちを停止します",
   )
   @app_commands.guild_only()
   async def stop_reading(self, interaction: discord.Interaction):
      self.stop(interaction.guild_id)
      await interaction.response.send_message(
         "読み上げを停止しました。", ephemeral=True
      )

   @app_commands.command(
      name="朗読", description="指定したテキストを読み上げます"
   )
   @app_commands.guild_only()
   @app_commands.describe(text="読み上げるテキスト（最大100文字）")
   async def read(self, interaction: discord.Interaction, text: str):
      if not text.strip() or len(text) > 100:
         await interaction.response.send_message(
            "1〜100文字で入力してください。", ephemeral=True
         )
         return
      try:
         self.enqueue(interaction.guild_id, text)
         message = "読み上げを受け付けました。"
      except ValueError as error:
         message = str(error)
      await interaction.response.send_message(message, ephemeral=True)

   @app_commands.command(
      name="読み上げ辞書更新",
      description="ローカルCSVまたはGoogle Sheetsから辞書を更新します",
   )
   @app_commands.guild_only()
   async def reload_dictionary(self, interaction: discord.Interaction):
      await interaction.response.defer(ephemeral=True, thinking=True)
      try:
         count = await asyncio.to_thread(self.synth.load_dictionary)
         message = f"辞書を更新しました（{count}件）。"
      except Exception:
         message = (
            "辞書を更新できません。"
            "CSVまたはGoogle Sheetsの接続設定を確認してください。"
         )
      await interaction.edit_original_response(content=message)

   @app_commands.command(
      name="se追加", description="読み上げ文中の単語で再生するSEを登録します"
   )
   @app_commands.guild_only()
   @app_commands.rename(
      audio_file="音声ファイル",
      word="トリガー",
      volume="音量",
      priority="優先度",
   )
   async def add_sound(
      self,
      interaction: discord.Interaction,
      audio_file: discord.Attachment,
      word: str,
      volume: float = 0.1,
      short: int = 1,
      priority: int = 1,
   ):
      suffix = Path(audio_file.filename).suffix.lower()
      if (
         not word.strip()
         or suffix not in SOUND_EXTENSIONS
         or not 0 <= volume <= 1
         or short not in (0, 1)
      ):
         await interaction.response.send_message(
            "単語・音声形式・音量0〜1・short 0/1を確認してください。",
            ephemeral=True,
         )
         return
      if audio_file.size > 20 * 1024 * 1024:
         await interaction.response.send_message(
            "音声ファイルは20MiB以下にしてください。", ephemeral=True
         )
         return
      await interaction.response.defer(ephemeral=True, thinking=True)
      relative = f"audio/se_{uuid4().hex}{suffix}"
      path = self.config.data_dir / relative
      try:
         await audio_file.save(path)
         self.store.add_sound(word.strip(), relative, volume, short, priority)
      except Exception:
         path.unlink(missing_ok=True)
         await interaction.edit_original_response(
            content="SEを登録できませんでした。"
         )
         return
      await interaction.edit_original_response(
         content=f"SEを登録しました。トリガー: {word}",
         allowed_mentions=discord.AllowedMentions.none(),
      )

   @commands.Cog.listener()
   async def on_message(self, message: discord.Message):
      if message.author.bot or message.guild is None:
         return
      settings = self.store.settings(message.guild.id)
      if (
         not settings["read_enabled"]
         or message.channel.id != settings["read_channel_id"]
      ):
         return
      text = sanitize(message.clean_content, bool(message.attachments))
      if text:
         try:
            self.enqueue(message.guild.id, text)
         except ValueError:
            pass

   async def restore(self, guild_id: int):
      session = self.session(guild_id)
      if self.closing or session.manual_disconnect:
         return
      async with session.connection_lock:
         if self.closing or session.manual_disconnect:
            return
         voice = self.voice(guild_id)
         if voice and voice.is_connected():
            return
         if (
            session.reconnect_failures >= 3
            and time.monotonic() - session.last_reconnect < 60
         ):
            return
         channel = self.bot.get_channel(
            self.store.settings(guild_id)["voice_channel_id"]
         )
         if not isinstance(channel, discord.VoiceChannel):
            return
         session.last_reconnect = time.monotonic()
         try:
            if voice:
               await voice.disconnect(force=True)
            await channel.connect(timeout=20, reconnect=True)
            session.reconnect_failures = 0
         except (discord.DiscordException, OSError, asyncio.TimeoutError):
            session.reconnect_failures += 1
            LOGGER.warning("Voice reconnect failed in guild %s", guild_id)

   @commands.Cog.listener()
   async def on_ready(self):
      for settings in self.store.connected_guilds():
         await self.restore(settings["guild_id"])

   @commands.Cog.listener()
   async def on_voice_state_update(self, member, before, after):
      if self.closing:
         return
      if member.id == self.bot.user.id:
         if after.channel is not None:
            self.store.update_settings(
               member.guild.id, voice_channel_id=after.channel.id
            )
            return
         if before.channel and after.channel is None:
            await asyncio.sleep(3)
            await self.restore(member.guild.id)
         return
      if member.bot:
         return
      await self.restore(member.guild.id)
      voice = self.voice(member.guild.id)
      if (
         voice
         and after.channel == voice.channel
         and before.channel != after.channel
      ):
         entrance = self.store.entrance(member.id)
         if entrance:
            try:
               clip = Clip(
                  self.synth.audio_path(entrance["se"]),
                  entrance["volume"],
               )
               self.enqueue(member.guild.id, "", clip=clip)
            except ValueError:
               LOGGER.warning("Entrance sound unavailable")


async def setup(bot):
   await bot.add_cog(Speech(bot))
