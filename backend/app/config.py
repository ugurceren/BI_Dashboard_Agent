"""Uygulama ayarları: `.env` veya ortam değişkenleri.

Model, veri kaynağı ve sözlük kaynağı tamamen ayardan değişir; kod modelden bağımsızdır.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM (OpenAI uyumlu: vLLM, Ollama, LM Studio, LiteLLM ...)
    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "Qwen/Qwen3-32B"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096
    llm_timeout_s: float = 180
    # native: sunucunun tools desteği | prompt: araçlar sistem mesajında, <tool_call> etiketiyle
    # auto: native dene, sunucu desteklemiyorsa prompt'a düş
    llm_tool_mode: Literal["auto", "native", "prompt"] = "auto"
    llm_strip_thinking: bool = True  # Qwen3 <think> bloklarını at
    # Sunucuya özel ek parametreler (JSON), ör. vLLM + Qwen3 düşünme modunu kapatmak için:
    # LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
    llm_extra_body: dict | None = None

    # --- Görsel (vision) model: örnek dashboard görselini okumak için. Boşsa yalnızca renk çıkarımı yapılır.
    vision_base_url: str | None = None  # boşsa llm_base_url
    vision_api_key: str | None = None
    vision_model: str | None = None

    # --- Veri kaynağı: Microsoft SQL Server (salt-okunur kullanıcı önerilir)
    sqlserver_odbc: str = ("DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;DATABASE=AdventureWorksDW2025;"
                           "Trusted_Connection=yes;TrustServerCertificate=yes;")
    query_timeout_s: float = 30
    preview_rows: int = 50
    max_rows: int = 5000

    # --- Veri sözlüğü: nereden, hangi sorgularla okunacağı config/dictionary.toml'da
    dictionary_config: Path = BACKEND_DIR / "config" / "dictionary.toml"
    policy_config: Path = BACKEND_DIR / "config" / "policy.toml"
    user_role: str = "analyst"  # ileride AD/Entra ID'den gelecek

    # --- Harness
    max_agent_steps: int = 24      # tek kullanıcı mesajında en fazla LLM çağrısı
    tool_result_char_limit: int = 6000
    sessions_dir: Path = BACKEND_DIR / "sessions"
    audit_log: Path = BACKEND_DIR / "logs" / "audit.jsonl"
    viewer_html: Path = BACKEND_DIR.parent / "frontend" / "dist-viewer" / "viewer.html"
    demo_spec: Path = BACKEND_DIR.parent / "docs" / "demo_spec.json"  # "Demo dashboard yükle" butonu
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("dictionary_config", "policy_config", "sessions_dir", "audit_log",
                     "viewer_html", "demo_spec", mode="after")
    @classmethod
    def _relative_to_backend(cls, v: Path) -> Path:
        # .env'deki göreli yollar, sunucu hangi klasörden başlatılırsa başlatılsın backend/'e göre çözülür
        return v if v.is_absolute() else (BACKEND_DIR / v).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)
