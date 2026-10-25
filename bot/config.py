import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Config:
    token: str = os.getenv("BOT_TOKEN", "")
    db_path: str = os.getenv("DB_PATH", str(ROOT / "data" / "trading.db"))
    alert_interval: int = int(os.getenv("ALERT_INTERVAL", "10"))
    allowed_users: frozenset[int] = field(
        default_factory=lambda: frozenset(
            int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x
        )
    )


config = Config()
