"""Tests for the YouTube API settings."""
from app.core.utils.config import Settings


class TestYoutubeSettings:
    def test_defaults_to_none(self):
        settings = Settings(_env_file=None, GOOGLE_YOUTUBE_API_BASE_URL=None, GOOGLE_YOUTUBE_API_KEY=None)

        assert settings.GOOGLE_YOUTUBE_API_BASE_URL is None
        assert settings.GOOGLE_YOUTUBE_API_KEY is None

    def test_reads_values_from_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_YOUTUBE_API_BASE_URL", "https://yt.example/v3")
        monkeypatch.setenv("GOOGLE_YOUTUBE_API_KEY", "key123")

        settings = Settings(_env_file=None)

        assert settings.GOOGLE_YOUTUBE_API_BASE_URL == "https://yt.example/v3"
        assert settings.GOOGLE_YOUTUBE_API_KEY == "key123"
