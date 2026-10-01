"""Load the git-ignored .env for the command-line tools; variables already set in the process win."""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

BACKEND_ENV = Path(__file__).resolve().parents[2] / ".env"  # backend/.env


def load_env():
    """Use the nearest .env at or above the working directory, else backend/.env. Returns the path used, if any."""
    path = find_dotenv(usecwd=True) or (str(BACKEND_ENV) if BACKEND_ENV.is_file() else "")
    if path:
        load_dotenv(path, override=False)
    return path or None
