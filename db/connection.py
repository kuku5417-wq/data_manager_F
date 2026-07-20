"""db/connection.py — MSSQL(운영서버) 연결 (.env DB_* / 사내망 전용).

키(사용자 지정): DB_SEVER(서버호스트, 오타 보이나 그대로 + DB_SERVER 폴백) · DB_PORT · DB_NAME ·
DB_USERNAME · DB_PASSWORD · DB_DRIVER(기본 "ODBC Driver 18 for SQL Server").
드라이버/엔진은 호출 시점 lazy import — 미설치·서버 부재에도 import 자체는 안전.
"""
from __future__ import annotations

from functools import lru_cache

import path_config as pc

_PLACEHOLDER = {"", "0.0.0.0", "0000", "0"}


def _odbc_braced(v: str) -> str:
    """ODBC 연결문자열 값 이스케이프 — {}로 감싸고 내부 }는 }}로 (;·{} 포함 비밀번호 대응)."""
    return "{" + str(v).replace("}", "}}") + "}"


def _cfg() -> dict:
    return {
        "host":   pc.read_secret("DB_SEVER") or pc.read_secret("DB_SERVER"),
        "port":   pc.read_secret("DB_PORT") or "1433",
        "name":   pc.read_secret("DB_NAME"),
        "user":   pc.read_secret("DB_USERNAME"),
        "pwd":    pc.read_secret("DB_PASSWORD"),
        "driver": pc.read_secret("DB_DRIVER") or "ODBC Driver 18 for SQL Server",
    }


def is_configured() -> bool:
    """운영서버 접속정보가 실제로 채워졌는지(placeholder 제외). 미설정이면 전체 휴면."""
    c = _cfg()
    return (c["host"] not in _PLACEHOLDER) and bool(c["name"]) and bool(c["user"])


def status_text() -> str:
    c = _cfg()
    if is_configured():
        return f"운영서버 설정됨 — {c['host']}:{c['port']} / {c['name']}"
    return "운영서버 미설정 (.env DB_SEVER/DB_PORT/DB_NAME/DB_USERNAME/DB_PASSWORD 입력 시 활성)"


@lru_cache(maxsize=1)
def get_engine():
    """sqlalchemy MSSQL 엔진 (mssql+pyodbc). pyodbc lazy — 미설치 시 여기서만 예외.

    엔진은 lru_cache 로 단일 재사용 — 호출마다 새 커넥션 풀이 누적되는 것 방지.
    """
    from urllib.parse import quote_plus
    from sqlalchemy import create_engine
    c = _cfg()
    odbc = quote_plus(
        f"DRIVER={{{c['driver']}}};SERVER={c['host']},{c['port']};DATABASE={c['name']};"
        f"UID={_odbc_braced(c['user'])};PWD={_odbc_braced(c['pwd'])};TrustServerCertificate=yes"
    )
    return create_engine(f"mssql+pyodbc:///?odbc_connect={odbc}", pool_pre_ping=True)
