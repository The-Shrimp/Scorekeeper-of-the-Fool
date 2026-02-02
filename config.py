"""
config.py
Loads environment variables and exposes configuration.

Best practice:
- Keep secrets out of code; load them via .env or environment variables.
"""

import os
from dotenv import load_dotenv

def load_config() -> dict:
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing. Add it to your .env or environment.")
    return {"DISCORD_BOT_TOKEN": token}
