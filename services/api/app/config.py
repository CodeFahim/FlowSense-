"""Application settings loaded from environment variables.

Production refuses to start with demo controls on, insecure cookies, or a weak
sandbox event secret (fail safely when critical settings are missing).

"hosted_demo" is for a public, login-protected sandbox demonstration (synthetic
data only). It keeps the demo accounts and demo controls, but unlike
"development" it requires HTTPS-only cookies and a strong generated demo
password, and it hides the API docs. It is not a production configuration.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIFELINE_", extra="ignore")

    env: Literal["development", "test", "hosted_demo", "production"] = "development"
    database_url: str
    session_ttl_minutes: int = 480
    approval_ttl_minutes: int = 30
    login_max_failures: int = 5
    login_window_minutes: int = 15
    sandbox_event_secret: SecretStr
    demo_controls_enabled: bool = True
    demo_password: SecretStr | None = None
    cookie_secure: bool = False
    # Password for the designated clean ("Start fresh") submission account. Optional; when unset
    # that account is not created. Same strength rules as the demo password.
    fresh_account_password: SecretStr | None = None
    # Accounts
    signup_enabled: bool = True
    signups_per_hour: int = 100  # global cap across all networks
    # Proxies in front of the API that append to X-Forwarded-For and that a caller cannot bypass.
    # Render's own proxy = 1 (the right-most entry). 0 = use the socket peer.
    trusted_proxy_hops: int | None = None  # unset: 1 on hosted_demo/production (behind Render), else 0
    # Shared secret the web frontend (proxy.ts) sends with the client IP it saw. Optional; when unset the
    # right-most hop is used (all traffic through Vercel then shares Vercel's egress addresses).
    proxy_secret: SecretStr | None = None
    password_min_length: int = 12
    reset_token_minutes: int = 30
    verify_token_hours: int = 24
    # Email (password reset, address verification). "off" = not configured: nothing is sent and the
    # UI says so; delivery is never simulated as successful.
    # "smtp" = any SMTP server, e.g. Gmail (smtp.gmail.com:465 with an app password); "resend" = Resend HTTP API.
    # "brevo" = Brevo HTTPS API (works on hosts that block SMTP ports, e.g. Render's free plan; a single
    # verified sender address such as a Gmail address is enough, no domain needed).
    email_provider: Literal["off", "resend", "smtp", "brevo"] = "off"
    resend_api_key: SecretStr | None = None
    brevo_api_key: SecretStr | None = None
    email_from: str | None = None  # sender address, e.g. teamlifeline01@gmail.com (or "Name <address>" for Resend)
    email_from_name: str = "FlowSense — No Reply"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465  # 465 = TLS from the start; 587 = STARTTLS
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None  # a Gmail *app password*, set only in the server's environment
    public_app_url: str | None = None  # trusted origin used in emailed links, e.g. https://lifeline-rescue-psi.vercel.app
    email_timeout_seconds: float = 8.0
    # AI assistant. demo = offline deterministic assistant (labelled); live = a real model with server-side
    # tools; off = assistant unavailable. In live mode, when the AI service is out of quota or down, the
    # answer comes from FlowSense's own calculations instead, labelled as such (ai_auto_fallback); it is
    # never presented as an AI answer.
    ai_mode: Literal["demo", "live", "off"] = "demo"
    # Which live model service: "anthropic" (Messages API) or "gemini" (Google Gemini API, has a free tier).
    ai_provider: Literal["anthropic", "gemini"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None  # e.g. a current "Flash" model listed in Google AI Studio
    # How much the Gemini model "thinks" before answering. FlowSense does the money maths itself, so "low"
    # keeps answers fast (free-tier thinking at the default level can exceed the time budget).
    gemini_thinking: Literal["low", "default"] = "low"
    # Other Gemini models to use, in order, when the main one is out of quota or overloaded. Each model has
    # its own free-tier quota. "auto" = discover the key's Flash models (Flash-Lite first); "off" = none;
    # or a comma-separated list of model IDs.
    gemini_fallback_models: str = "auto"
    ai_timeout_seconds: float = 45.0
    ai_max_tool_rounds: int = 3
    ai_max_output_tokens: int = 2048
    # Counts only answers produced by the AI service; quick answers from FlowSense's own calculations are free.
    ai_messages_per_user_per_day: int = 200
    # When the AI is out of quota, down, slow or the user's daily AI limit is reached, answer from FlowSense's
    # own calculations (labelled "Quick answer") instead of showing an error.
    ai_auto_fallback: bool = True
    # The same question on unchanged records within this many minutes reuses the earlier AI answer.
    ai_answer_cache_minutes: int = 15
    # Server-side refusal fallback (Anthropic beta): if the model declines, the API retries on a fallback model.
    ai_refusal_fallback: bool = True
    max_body_bytes: int = 1_000_000
    # Exchange rates for display and the converter only (never used to move money).
    # "erapi": ExchangeRate-API open access (free, no key, ~160 currencies incl. BDT, daily; attribution required).
    # "exchangerate_api": ExchangeRate-API v6 with LIFELINE_FX_API_KEY (keyed plan; more frequent updates).
    # "ecb": European Central Bank euro reference rates (free, no key, ~30 currencies, daily).
    # "off": no automatic rates; the converter still offers manual and labelled demo rates.
    fx_provider: Literal["erapi", "exchangerate_api", "ecb", "off"] = "erapi"
    # Used only when the primary provider has no snapshot at all; results always name the provider that answered.
    fx_fallback_provider: Literal["ecb", "off"] = "ecb"
    fx_api_key: SecretStr | None = None
    fx_refresh_hours: int = 6
    fx_stale_days: int = 4
    fx_timeout_seconds: float = 5.0

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        return normalize_database_url(v)

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if len(self.sandbox_event_secret.get_secret_value()) < 32:
            raise ValueError("LIFELINE_SANDBOX_EVENT_SECRET must be at least 32 characters")
        if self.env == "production":
            if self.demo_controls_enabled:
                raise ValueError("Demo controls must be disabled in production")
            if not self.cookie_secure:
                raise ValueError("Secure cookies are required in production")
            if self.demo_password is not None:
                raise ValueError("Demo credentials must not be configured in production")
        if self.env == "hosted_demo":
            if not self.cookie_secure:
                raise ValueError("A hosted demo is served over HTTPS: LIFELINE_COOKIE_SECURE must be true")
            if self.demo_password is None or len(self.demo_password.get_secret_value()) < 24:
                raise ValueError("A hosted demo needs a generated LIFELINE_DEMO_PASSWORD of at least 24 characters")
            if self.trusted_proxy_hops is not None and self.trusted_proxy_hops < 1:
                raise ValueError("A hosted demo runs behind a proxy: set LIFELINE_TRUSTED_PROXY_HOPS=1 (Render)")
            if self.fresh_account_password is not None and len(self.fresh_account_password.get_secret_value()) < 24:
                raise ValueError("A hosted demo needs LIFELINE_FRESH_ACCOUNT_PASSWORD of at least 24 characters (or leave it unset)")
        if self.env in ("hosted_demo", "production") and self.public_app_url and not self.public_app_url.startswith("https://"):
            raise ValueError("LIFELINE_PUBLIC_APP_URL must be an https:// origin outside local development")
        if self.public_app_url and not _ORIGIN.fullmatch(self.public_app_url):
            raise ValueError("LIFELINE_PUBLIC_APP_URL must be an origin such as https://example.com (no path)")
        if self.ai_mode == "live" and not (self.ai_key and self.ai_model):
            p = self.ai_provider.upper()
            raise ValueError(f"AI_MODE=live with AI_PROVIDER={self.ai_provider} requires LIFELINE_{p}_API_KEY and LIFELINE_{p}_MODEL")
        if self.fx_provider == "exchangerate_api" and not (self.fx_api_key and self.fx_api_key.get_secret_value().strip()):
            raise ValueError("FX_PROVIDER=exchangerate_api requires LIFELINE_FX_API_KEY")
        return self

    @property
    def ai_key(self) -> str:
        """The API key of the selected live AI provider (empty when not set). Never logged or returned."""
        secret = self.gemini_api_key if self.ai_provider == "gemini" else self.anthropic_api_key
        return secret.get_secret_value().strip() if secret else ""

    @property
    def ai_model(self) -> str:
        return ((self.gemini_model if self.ai_provider == "gemini" else self.anthropic_model) or "").strip()

    @property
    def proxy_hops(self) -> int:
        if self.trusted_proxy_hops is not None:
            return self.trusted_proxy_hops
        return 1 if self.env in ("hosted_demo", "production") else 0

    @property
    def email_configured(self) -> bool:
        if not (self.email_from or "").strip():
            return False
        if self.email_provider == "brevo":
            return bool(self.brevo_api_key and self.brevo_api_key.get_secret_value().strip())
        if self.email_provider == "smtp":
            return bool((self.smtp_username or "").strip() and self.smtp_password and self.smtp_password.get_secret_value().strip())
        return (
            self.email_provider == "resend"
            and bool(self.resend_api_key and self.resend_api_key.get_secret_value().strip())
            and bool(self.public_app_url)
        )


_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?")


def normalize_database_url(url: str) -> str:
    """Hosting providers hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg 3 driver name."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
