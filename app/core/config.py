import os
from functools import lru_cache

from dotenv import load_dotenv


class Settings:
    def __init__(self):
        load_dotenv()

        self.model_llm = self._get_required_env("MODEL_LLM")
        self.base_url = self._get_required_env("BASE_URL")
        self.openrouter_api_key = self._get_required_env("OPENROUTER_API_KEY")

        self.gitlab_url = self._get_required_env("GITLAB_URL")
        self.gitlab_token = self._get_required_env("GITLAB_TOKEN")
        self.gitlab_project_id = self._get_required_env("GITLAB_PROJECT_ID")

        self.jira_url = self._get_required_env("JIRA_URL")
        self.jira_email = self._get_required_env("JIRA_EMAIL")
        self.jira_api_token = self._get_required_env("JIRA_API_TOKEN")

        self.mattermost_url = self._get_required_env("MATTERMOST_URL")
        self.mattermost_bot_token = self._get_required_env("MATTERMOST_BOT_TOKEN")

        self.agent_context_path = os.getenv("AGENT_CONTEXT_PATH", "AGENT.md")

        self.log_dir = os.getenv("LOG_DIR", "logs")
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.log_file_name = os.getenv("LOG_FILE_NAME", "ai-dev-agent.log")
        self.log_backup_days = int(os.getenv("LOG_BACKUP_DAYS", "30"))

        self._validate_log_level()

    def _get_required_env(self, name: str) -> str:
        value = os.getenv(name)

        if not value:
            raise RuntimeError(f"Environment variable '{name}' is required")

        return value

    def _validate_log_level(self):
        allowed_levels = {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }

        if self.log_level not in allowed_levels:
            raise RuntimeError(
                f"Invalid LOG_LEVEL '{self.log_level}'. "
                f"Allowed values: {', '.join(sorted(allowed_levels))}"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
