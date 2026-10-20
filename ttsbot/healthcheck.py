import sys
import time

from .config import Config


def healthy() -> bool:
   path = Config.from_env().data_dir / "heartbeat"
   try:
      return 0 <= time.time() - float(path.read_text()) < 60
   except (OSError, ValueError):
      return False


if __name__ == "__main__":
   sys.exit(0 if healthy() else 1)
