import os
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

        self.agent_context_path = os.getenv("AGENT_CONTEXT_PATH", "AGENT.md")

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
        self.qdrant_prefer_grpc = os.getenv("QDRANT_PREFER_GRPC", "false").lower() in {"1", "true", "yes"}

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
