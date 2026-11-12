"""Settings, read from environment variables (or a .env file)."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./osteocare.db")
    # Supabase / Render give "postgres://" or "postgresql://"; use the psycopg 3 driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    database_url: str = _database_url()
    supabase_url: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    # Optional: only needed if your Supabase project still signs tokens with the legacy HS256 secret.
    supabase_jwt_secret: str = os.getenv("SUPABASE_JWT_SECRET", "")
    cors_origins: list[str] = [
        o.strip()
        for o in os.getenv(
            "CORS_ORIGINS",
            "https://osteocare-ai-mobility-monitor.packedsesame1.chatgpt.site,http://localhost:5173",
        ).split(",")
        if o.strip()
    ]
    # An insole counts as "online" if it sent a reading within this many seconds.
    online_window_seconds: int = int(os.getenv("ONLINE_WINDOW_SECONDS", "10"))
    # The frontend expects at most 50,000 samples per request.
    max_readings: int = int(os.getenv("MAX_READINGS", "50000"))


settings = Settings()
