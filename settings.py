"""settings.py — data_manager secret 접근 계층.

값은 사내망 고정 config(app_config.py, python-dotenv)에서 위임받는다.
복사 모듈(llm_client/db_connector/api_weather)이 `from settings import secrets` 로
쓰는 AppSecrets 인터페이스(secrets.*)는 그대로 유지한다.
키 이름은 실제 secret/.env 키(NAS_BASE_PATH/DB_MYSQL_*/LLM_SOLAR_*/HTTP(S)_PROXY 등).
(data_manager_F: 사내망 전용 — 사외망 로컬경로·Upstage/OpenAI 프로퍼티 제거)
"""
from __future__ import annotations

import path_config as pc   # import 시 상위 폴더를 sys.path에 추가 → app_config import 가능  # noqa: F401
import app_config as ac


class AppSecrets:
    """app_config 단일 소스를 typed property 로 노출. tbm AppSecrets 와 인터페이스 호환."""

    # ── 경로 ───────────────────────────────────────────
    @property
    def nas_base_path(self) -> str:
        return ac.NAS_BASE_PATH

    # ── MySQL ──────────────────────────────────────────
    @property
    def db_host(self) -> str:
        return ac.DB_MYSQL_HOST

    @property
    def db_port(self) -> int:
        try:
            return int(ac.DB_MYSQL_PORT or 3306)
        except ValueError:
            return 3306

    @property
    def db_user(self) -> str:
        return ac.DB_MYSQL_USER

    @property
    def db_password(self) -> str:
        return ac.DB_MYSQL_PASSWORD

    @property
    def db_database(self) -> str:
        return ac.DB_MYSQL_DATABASE

    @property
    def db_url(self) -> str | None:
        """SQLAlchemy URL. HOST 미설정 시 None → parquet 폴백."""
        return ac.db_url()

    # ── LLM / SOLA (사내망 전용) ───────────────────────
    @property
    def sola_endpoint(self) -> str:
        return ac.LLM_SOLAR_API_URL

    @property
    def sola_api_key(self) -> str:
        return ac.LLM_SOLAR_API_KEY

    @property
    def sola_model(self) -> str:
        return ac.LLM_SOLAR_MODEL

    # ── 프록시 / SSL (사내망 프록시) ────────────────────
    @property
    def proxy_url(self) -> str:
        return ac.HTTPS_PROXY or ac.HTTP_PROXY or ""

    @property
    def use_proxy(self) -> bool:
        return ac.USE_PROXY

    @property
    def proxies(self) -> dict | None:
        return ac.proxies()

    @property
    def ssl_verify(self) -> bool:
        return ac.SSL_VERIFY

    # ── 기상청 ─────────────────────────────────────────
    @property
    def weather_api_key(self) -> str:
        return ac.WEATHER_API_KEY

    @property
    def weather_base_url(self) -> str:
        return ac.WEATHER_BASE_URL


# 싱글톤 — 모든 모듈에서 이 인스턴스만 사용
secrets = AppSecrets()
