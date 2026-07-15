from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / "ecs" / ".env")

APP_NAME = os.environ.get("APP_NAME", "Agent1 Knowledge Base")
APP_VERSION = "7.14-final"

DATA_ROOT = Path(os.environ.get("DATA_ROOT", str(PROJECT_ROOT / "ecs-data"))).expanduser().resolve()
UPLOAD_ROOT = DATA_ROOT / "uploads"
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", str(DATA_ROOT / "agent_jobs.db"))).expanduser().resolve()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
WORKER_SHARED_SECRET = os.environ.get("WORKER_SHARED_SECRET", "")
WORKER_TIMEOUT = int(os.environ.get("WORKER_TIMEOUT", "240"))
FILE_COMMAND_TIMEOUT = int(os.environ.get("FILE_COMMAND_TIMEOUT", "60"))

ALLOWED_TEAMS = tuple(
    team.strip()
    for team in os.environ.get("ALLOWED_TEAMS", "team_a,team_b,team_c").split(",")
    if team.strip()
)

SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "agent1_session")
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "8"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower()
if COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    COOKIE_SAMESITE = "lax"

WX_TOKEN = os.environ.get("WXWORK_TOKEN", "")
WX_AES_KEY = os.environ.get("WXWORK_AESKEY", "")
WX_CORP_ID = os.environ.get("WXWORK_CORPID", "")
WX_AGENT_ID = os.environ.get("WXWORK_AGENTID", "")
WX_SECRET = os.environ.get("WXWORK_CORPSECRET", "")
WX_GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WX_SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


def ensure_directories() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
