import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///instance/stockmonitor.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "admin")
    DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "admin123")
    TOKEN_SALT = os.getenv("TOKEN_SALT", "stockmonitor-token")
    TOKEN_MAX_AGE_SECONDS = int(os.getenv("TOKEN_MAX_AGE_SECONDS", "86400"))
