"""
Shared configuration object read from environment variables and /agentforge/.env.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if it exists
_env_file = os.environ.get("ENV_FILE_PATH", "/agentforge/.env")
if Path(_env_file).exists():
    load_dotenv(_env_file, override=False)


class Config:
    # Forgejo
    forgejo_base_url: str = os.environ.get("FORGEJO_BASE_URL", "http://forgejo:3000")
    forgejo_api_token: str = os.environ.get("FORGEJO_API_TOKEN", "")
    forgejo_admin_user: str = os.environ.get("FORGEJO_ADMIN_USER", "")
    forgejo_admin_pass: str = os.environ.get("FORGEJO_ADMIN_PASS", "")
    forgejo_workspace_repo: str = os.environ.get("FORGEJO_WORKSPACE_REPO", "agentforge-workspace")

    # Orchestrator
    orchestrator_url: str = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000")

    # LangFuse
    langfuse_base_url: str = os.environ.get("LANGFUSE_BASE_URL", "http://langfuse:3000")
    langfuse_public_key: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.environ.get("LANGFUSE_SECRET_KEY_API", "")

    # LLM
    llm_provider: str = os.environ.get("LLM_PROVIDER", "")
    llm_model: str = os.environ.get("LLM_MODEL", "")

    # Agent models
    agent_supervisor_model: str = os.environ.get("AGENT_SUPERVISOR_MODEL", "")
    agent_architect_model: str = os.environ.get("AGENT_ARCHITECT_MODEL", "")
    agent_coder_model: str = os.environ.get("AGENT_CODER_MODEL", "")
    agent_tester_model: str = os.environ.get("AGENT_TESTER_MODEL", "")
    agent_reviewer_model: str = os.environ.get("AGENT_REVIEWER_MODEL", "")

    # API keys
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")

    # Paths
    env_file_path: str = os.environ.get("ENV_FILE_PATH", "/agentforge/.env")
    secrets_path: str = os.environ.get("SECRETS_PATH", "/run/secrets")

    def reload(self) -> None:
        """Re-read environment (and .env file) into instance attributes."""
        env_file = os.environ.get("ENV_FILE_PATH", "/agentforge/.env")
        if Path(env_file).exists():
            load_dotenv(env_file, override=True)

        self.forgejo_base_url = os.environ.get("FORGEJO_BASE_URL", "http://forgejo:3000")
        self.forgejo_api_token = os.environ.get("FORGEJO_API_TOKEN", "")
        self.forgejo_admin_user = os.environ.get("FORGEJO_ADMIN_USER", "")
        self.forgejo_admin_pass = os.environ.get("FORGEJO_ADMIN_PASS", "")
        self.forgejo_workspace_repo = os.environ.get("FORGEJO_WORKSPACE_REPO", "agentforge-workspace")
        self.orchestrator_url = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000")
        self.langfuse_base_url = os.environ.get("LANGFUSE_BASE_URL", "http://langfuse:3000")
        self.langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        self.langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY_API", "")
        self.llm_provider = os.environ.get("LLM_PROVIDER", "")
        self.llm_model = os.environ.get("LLM_MODEL", "")
        self.agent_supervisor_model = os.environ.get("AGENT_SUPERVISOR_MODEL", "")
        self.agent_architect_model = os.environ.get("AGENT_ARCHITECT_MODEL", "")
        self.agent_coder_model = os.environ.get("AGENT_CODER_MODEL", "")
        self.agent_tester_model = os.environ.get("AGENT_TESTER_MODEL", "")
        self.agent_reviewer_model = os.environ.get("AGENT_REVIEWER_MODEL", "")
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        self.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.env_file_path = os.environ.get("ENV_FILE_PATH", "/agentforge/.env")
        self.secrets_path = os.environ.get("SECRETS_PATH", "/run/secrets")

    @property
    def forgejo_api_base(self) -> str:
        return f"{self.forgejo_base_url}/api/v1"

    @property
    def active_api_key(self) -> str:
        """Return the API key for the currently configured LLM provider."""
        provider = self.llm_provider.lower() if self.llm_provider else ""
        if provider == "anthropic":
            return self.anthropic_api_key
        if provider == "openai":
            return self.openai_api_key
        if provider == "openrouter":
            return self.openrouter_api_key
        return ""

    @property
    def is_configured(self) -> bool:
        return bool(self.llm_provider and self.active_api_key)

    def forgejo_auth_headers(self) -> dict:
        if self.forgejo_api_token:
            return {"Authorization": f"token {self.forgejo_api_token}"}
        if self.forgejo_admin_user and self.forgejo_admin_pass:
            import base64
            creds = base64.b64encode(
                f"{self.forgejo_admin_user}:{self.forgejo_admin_pass}".encode()
            ).decode()
            return {"Authorization": f"Basic {creds}"}
        return {}


# Singleton
config = Config()
