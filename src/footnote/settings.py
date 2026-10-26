from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FOOTNOTE_", env_file=".env", extra="ignore")

    # The SEC asks every client to identify itself with a name and contact email.
    user_agent: str = "Footnote research project jagadishbutukuri11@gmail.com"
    since_year: int = 2021
    forms: tuple[str, ...] = ("10-K",)

    # Where filings go: an S3 bucket in AWS, or a folder when running locally.
    bucket: str = ""
    local_dir: str = "out/store"
