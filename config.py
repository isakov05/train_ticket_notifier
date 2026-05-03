from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "300"))
DB_PATH: str = os.getenv("DB_PATH", "tickets.db")
