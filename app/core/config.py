import os
import re
import tempfile
from functools import lru_cache

from dotenv import load_dotenv


class Settings:
    def __init__(self):
        load_dotenv()

        self.model_llm = self._get_required_env("MODEL_LLM")
        self.base_url = self._get_required_env("BASE_URL")
        self.openrouter_api_key = self._get_required_env("OPENROUTER_API_KEY")
        self.llm_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "5000"))

        self.gitlab_url = self._get_required_env("GITLAB_URL")
        self.gitlab_token = self._get_required_env("GITLAB_TOKEN")
        self.gitlab_project_id = self._get_required_env("GITLAB_PROJECT_ID")

        self.jira_url = self._get_required_env("JIRA_URL")
        self.jira_email = self._get_required_env("JIRA_EMAIL")
        self.jira_api_token = self._get_required_env("JIRA_API_TOKEN")

        self.mattermost_url = self._get_required_env("MATTERMOST_URL")
        self.mattermost_bot_token = self._get_required_env("MATTERMOST_BOT_TOKEN")

        self.mattermost_pf_scout_enabled = self._env_bool(
            "MATTERMOST_PF_SCOUT_ENABLED",
            default=False,
        )
        self.mattermost_pf_scout_bot_token = os.getenv(
            "MATTERMOST_PF_SCOUT_BOT_TOKEN",
            self.mattermost_bot_token,
        ).strip()
        self.mattermost_pf_scout_project_key = os.getenv(
            "MATTERMOST_PF_SCOUT_PROJECT_KEY",
            "packetfence",
        ).strip()

        self.mattermost_code_reviewer_enabled = self._env_bool(
            "MATTERMOST_CODE_REVIEWER_ENABLED",
            default=False,
        )
        self.mattermost_code_reviewer_bot_token = os.getenv(
            "MATTERMOST_CODE_REVIEWER_BOT_TOKEN",
            self.mattermost_bot_token,
        ).strip()
        self.mattermost_code_reviewer_jira_key_pattern = os.getenv(
            "MATTERMOST_CODE_REVIEWER_JIRA_KEY_PATTERN",
            r"[A-Z][A-Z0-9]+-\d+",
        ).strip()

        self.mattermost_bot_reconnect_seconds = int(
            os.getenv("MATTERMOST_BOT_RECONNECT_SECONDS", "5")
        )
        self.mattermost_bot_worker_count = int(
            os.getenv("MATTERMOST_BOT_WORKER_COUNT", "2")
        )
        self.mattermost_bot_max_post_chars = int(
            os.getenv("MATTERMOST_BOT_MAX_POST_CHARS", "12000")
        )

        self.agent_context_path = os.getenv("AGENT_CONTEXT_PATH", "AGENT.md")

        self.database_url = os.getenv("DATABASE_URL", "").strip()

        self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "hashing").lower()
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.embedding_api_key = os.getenv("EMBEDDING_API_KEY", self.openrouter_api_key)
        self.embedding_base_url = os.getenv("EMBEDDING_BASE_URL", self.base_url)

        embedding_dimensions = os.getenv("EMBEDDING_DIMENSIONS", "").strip()
        self.embedding_dimensions = int(embedding_dimensions) if embedding_dimensions else None
        self.embedding_batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

        self.vector_store_provider = os.getenv("VECTOR_STORE_PROVIDER", "memory").lower()
        self.qdrant_url = os.getenv("QDRANT_URL", "")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY", "")
        self.qdrant_collection_name = os.getenv("QDRANT_COLLECTION_NAME", "codebase_chunks")
        self.qdrant_local_path = os.getenv(
            "QDRANT_LOCAL_PATH",
            os.path.join(tempfile.gettempdir(), "ai-dev-agent-qdrant"),
        )
        self.qdrant_prefer_grpc = os.getenv("QDRANT_PREFER_GRPC", "false").lower() in {
            "1",
            "true",
            "yes",
        }

        self.log_dir = os.getenv("LOG_DIR", "logs")
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.log_file_name = os.getenv("LOG_FILE_NAME", "ai-dev-agent.log")
        self.log_backup_days = int(os.getenv("LOG_BACKUP_DAYS", "30"))

        self._validate_log_level()
        self._validate_mattermost_bots()

    def _get_required_env(self, name: str) -> str:
        value = os.getenv(name)

        if not value:
            raise RuntimeError(f"Environment variable '{name}' is required")

        return value

    def _env_bool(self, name: str, default: bool = False) -> bool:
        value = os.getenv(name)

        if value is None:
            return default

        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

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

    def _validate_mattermost_bots(self) -> None:
        if self.mattermost_pf_scout_enabled and not self.mattermost_pf_scout_bot_token:
            raise RuntimeError(
                "MATTERMOST_PF_SCOUT_BOT_TOKEN is required when "
                "MATTERMOST_PF_SCOUT_ENABLED=true"
            )

        if self.mattermost_pf_scout_enabled and not self.mattermost_pf_scout_project_key:
            raise RuntimeError(
                "MATTERMOST_PF_SCOUT_PROJECT_KEY is required when "
                "MATTERMOST_PF_SCOUT_ENABLED=true"
            )

        if (
            self.mattermost_code_reviewer_enabled
            and not self.mattermost_code_reviewer_bot_token
        ):
            raise RuntimeError(
                "MATTERMOST_CODE_REVIEWER_BOT_TOKEN is required when "
                "MATTERMOST_CODE_REVIEWER_ENABLED=true"
            )

        if (
            self.mattermost_code_reviewer_enabled
            and not self.mattermost_code_reviewer_jira_key_pattern
        ):
            raise RuntimeError(
                "MATTERMOST_CODE_REVIEWER_JIRA_KEY_PATTERN is required when "
                "MATTERMOST_CODE_REVIEWER_ENABLED=true"
            )

        try:
            re.compile(self.mattermost_code_reviewer_jira_key_pattern)
        except re.error as exc:
            raise RuntimeError(
                "Invalid MATTERMOST_CODE_REVIEWER_JIRA_KEY_PATTERN: "
                f"{exc}"
            ) from exc

        if self.mattermost_bot_reconnect_seconds < 1:
            raise RuntimeError("MATTERMOST_BOT_RECONNECT_SECONDS must be >= 1")

        if self.mattermost_bot_worker_count < 1:
            raise RuntimeError("MATTERMOST_BOT_WORKER_COUNT must be >= 1")

        if self.mattermost_bot_max_post_chars < 1000:
            raise RuntimeError("MATTERMOST_BOT_MAX_POST_CHARS must be >= 1000")


@lru_cache
def get_settings() -> Settings:
    return Settings()
