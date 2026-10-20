import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
   data_dir: Path
   ffmpeg: str = "ffmpeg"
   queue_limit: int = 32
   sheet_id: str = ""
   sheet_name: str = ""
   service_account: Path = PROJECT_ROOT / "secrets" / "service_account.json"
   command_guild_id: int | None = None

   @classmethod
   def from_env(cls):
      data = Path(os.getenv("DATA_DIR", "./data")).expanduser()
      if not data.is_absolute():
         data = PROJECT_ROOT / data
      service_account = Path(
         os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_FILE", "./secrets/service_account.json"
         )
      )
      if not service_account.is_absolute():
         service_account = PROJECT_ROOT / service_account
      limit = int(os.getenv("QUEUE_LIMIT", "32"))
      guild = os.getenv("COMMAND_GUILD_ID", "").strip()
      guild_id = int(guild) if guild else None
      if limit < 1 or (guild_id is not None and guild_id <= 0):
         raise ValueError(
            "QUEUE_LIMITとCOMMAND_GUILD_IDには正の整数を指定してください。"
         )
      return cls(
         data.resolve(),
         os.getenv("FFMPEG_EXECUTABLE", "ffmpeg"),
         limit,
         os.getenv("GOOGLE_SHEET_ID", "").strip(),
         os.getenv("GOOGLE_SHEET_NAME", "").strip(),
         service_account,
         guild_id,
      )

   def prepare(self):
      self.data_dir.mkdir(parents=True, exist_ok=True)
      for name in ("audio", "cache"):
         (self.data_dir / name).mkdir(exist_ok=True)
      dictionary = self.data_dir / "dictionary.csv"
      if not dictionary.exists():
         dictionary.write_text("word,reading,priority\n", encoding="utf-8")
