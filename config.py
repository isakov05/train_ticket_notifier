from datetime import timezone, timedelta
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "300"))
DB_PATH: str = os.getenv("DB_PATH", "tickets.db")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "1488027742"))
TZ = timezone(timedelta(hours=5))  # Tashkent (UTC+5)
