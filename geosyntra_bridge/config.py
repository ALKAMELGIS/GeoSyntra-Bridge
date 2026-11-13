"""Runtime configuration (environment variables, all optional)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_list(name: str) -> List[str]:
    v = os.environ.get(name, "")
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Settings:
    root: str = ROOT
    schemas_dir: str = os.environ.get("GSB_SCHEMAS_DIR", os.path.join(ROOT, "schemas"))
    # extra folders whose schemas are also offered (e.g. schemas/examples — the generic demo network schemas)
    extra_schema_dirs: List[str] = field(default_factory=lambda: _env_list("GSB_EXTRA_SCHEMA_DIRS"))
    rules_dir: str = os.environ.get("GSB_RULES_DIR", os.path.join(ROOT, "mapping_rules"))
    data_dir: str = os.environ.get("GSB_DATA_DIR", os.path.join(ROOT, "data"))
    max_upload_mb: int = int(os.environ.get("GSB_MAX_UPLOAD_MB", "500"))
    job_ttl_hours: float = float(os.environ.get("GSB_JOB_TTL_HOURS", "72"))
    workers: int = int(os.environ.get("GSB_WORKERS", "2"))
    # auth: "none" (local/default), "token" (Bearer tokens in GSB_API_TOKENS),
    # "header" (trust the platform's reverse proxy user header, e.g. X-Forwarded-User)
    auth_mode: str = os.environ.get("GSB_AUTH_MODE", "none")
    api_tokens: List[str] = field(default_factory=lambda: _env_list("GSB_API_TOKENS"))
    user_header: str = os.environ.get("GSB_USER_HEADER", "X-Forwarded-User")
    admin_users: List[str] = field(default_factory=lambda: _env_list("GSB_ADMIN_USERS"))
    cors_origins: List[str] = field(default_factory=lambda: _env_list("GSB_CORS_ORIGINS"))
    base_path: str = os.environ.get("GSB_BASE_PATH", "").rstrip("/")   # mount under the platform, e.g. /tools/cad2gdb

    @property
    def jobs_dir(self) -> str:
        return os.path.join(self.data_dir, "jobs")


settings = Settings()
