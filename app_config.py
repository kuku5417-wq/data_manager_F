"""app_config.py — 환경설정 (data_manager_F: 사내망 전용 고정 사본).

★ 이 파일은 data_manager_F 전용 **사내망 고정** 사본이다. 원본 app_config(전 repo
  동일본 유지 원칙)에서 **의도적으로 분기**했다 — 사외망 자동감지·로컬 경로·프록시
  OFF·Upstage/OpenAI 폴백을 제거하고 사내망(NAS·프록시 ON·SOLA)으로 고정.

단일 소스: 상위 폴더의 secret/.env (python-dotenv 로드, 상위 탐색·SHI_ENV_FILE 지정 가능).
IS_INTERNAL 은 항상 True. NAS_BASE_PATH 는 사내망 NAS 전체 경로(필수).

키 이름은 실제 .env 키와 1:1 (NAS_BASE_PATH / NAS_*PATH / DB_MYSQL_* / DB_* (MSSQL) /
LLM_SOLAR_* / OCR_SOLAR_*·OCR_DOXA_* / HTTP_PROXY·HTTPS_PROXY / WEATHER_*).

프록시 정책: 사내망은 외부 인터넷 호출에 회사 프록시 사용. 사내 서버(sola/DoXA/MySQL/
MSSQL 등) 호출부는 proxies={"http":None,"https":None} 을 명시해 프록시를 우회할 것
(내부 IP가 외부 프록시에 막혀 403 차단되는 문제 방지).
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_env_file() -> str:
    """secret/.env 위치를 포터블하게 탐색.

    우선순위: 환경변수 SHI_ENV_FILE → app_config.py 위치에서 위로 올라가며 첫 secret/.env.
    (사내망에서 임의 경로에 압축해제해도 동작 — F:\\code 하드코드 제거.)
    """
    ov = os.getenv("SHI_ENV_FILE")
    if ov and Path(ov).exists():
        return ov
    here = Path(__file__).resolve()
    for d in (here.parent, *here.parents):
        cand = d / "secret" / ".env"
        if cand.exists():
            return str(cand)
    return str(here.parent / "secret" / ".env")   # 기본값(없어도 무해)


# .env 로드 (python-dotenv). 미설치 환경 대비 안전 처리.
try:
    from dotenv import load_dotenv
    load_dotenv(_find_env_file())
except Exception:
    pass


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── 망 (사내망 고정) ────────────────────────────────────
# data_manager_F 는 사내망 전용. 자동감지/VDI 없이 IS_INTERNAL=True 로 고정한다.
IS_INTERNAL   = True
ENV_LABEL     = "사내망"
IS_VDI        = False   # 하위호환용 상수(사내망 고정본은 항상 프록시 ON)

# ── 경로 (사내망 NAS 전용) ──────────────────────────────
# NAS_BASE_PATH = 사내망 NAS 전체 경로(…\06_Commissioning_TEAM). 필수.
NAS_BASE_PATH = _get("NAS_BASE_PATH")
if not NAS_BASE_PATH:
    raise RuntimeError(
        "NAS_BASE_PATH 미설정 — data_manager_F 는 사내망 전용입니다. "
        "secret/.env 에 NAS_BASE_PATH(사내망 NAS 전체 경로)를 설정하세요."
    )
BASE_PATH    = NAS_BASE_PATH
UPLOAD_PATH  = _get("NAS_UPLOAD_PATH")  or os.path.join(BASE_PATH, "upload")
PARQUET_PATH = _get("NAS_PARQUET_PATH") or os.path.join(BASE_PATH, "parquet")
DUMMY_PATH   = _get("NAS_DUMMY_PATH")   or os.path.join(BASE_PATH, "dummy")

# ── MySQL ───────────────────────────────────────────────
DB_MYSQL_HOST     = _get("DB_MYSQL_HOST")
DB_MYSQL_PORT     = _get("DB_MYSQL_PORT", "3306")
DB_MYSQL_DATABASE = _get("DB_MYSQL_DATABASE")
DB_MYSQL_USER     = _get("DB_MYSQL_USER")
DB_MYSQL_PASSWORD = _get("DB_MYSQL_PASSWORD")
USE_PARQUET = (not IS_INTERNAL) or (not DB_MYSQL_HOST) or DB_MYSQL_HOST.startswith("YOUR_")


def db_url() -> str | None:
    """SQLAlchemy URL. HOST 미설정/placeholder면 None → parquet 폴백."""
    if USE_PARQUET or not DB_MYSQL_HOST:
        return None
    return (f"mysql+pymysql://{DB_MYSQL_USER}:{DB_MYSQL_PASSWORD}"
            f"@{DB_MYSQL_HOST}:{DB_MYSQL_PORT}/{DB_MYSQL_DATABASE}?charset=utf8mb4")


# ── LLM (사내망 SOLA 고정) ──────────────────────────────
LLM_SOLAR_API_KEY = _get("LLM_SOLAR_API_KEY")
LLM_SOLAR_API_URL = _get("LLM_SOLAR_API_URL")
LLM_SOLAR_MODEL   = _get("LLM_SOLAR_MODEL")   # OpenAI 호환 model명(예: solar-1-mini-chat). 비면 생략
LLM_API_KEY, LLM_API_URL = LLM_SOLAR_API_KEY, LLM_SOLAR_API_URL

# ── OCR (사내망 SOLA/DoXA 고정) ─────────────────────────
OCR_SOLAR_API_KEY = _get("OCR_SOLAR_API_KEY")
OCR_SOLAR_API_URL = _get("OCR_SOLAR_API_URL")
OCR_SOLAR_MODEL   = _get("OCR_SOLAR_MODEL")   # 사내망 sola는 OpenAI SDK(vision) 모델명 필요
OCR_DOXA_API_KEY  = _get("OCR_DOXA_API_KEY")
OCR_DOXA_API_URL  = _get("OCR_DOXA_API_URL")
OCR_API_KEY, OCR_API_URL, OCR_MODEL = OCR_SOLAR_API_KEY, OCR_SOLAR_API_URL, OCR_SOLAR_MODEL

# ── 프록시 / SSL (사내망 — 외부 인터넷 호출은 회사 프록시 경유) ──
def _normalize_proxy(v: str | None) -> str | None:
    """프록시 URL 정규화 — 스킴(http://) 없으면 부착. httpx는 스킴 없는 프록시를
    'Unknown scheme for proxy URL' 예외로 거부하므로(requests와 달리), 여기서 통일한다.
    빈값/미설정은 None, 이미 스킴이 있으면 그대로."""
    v = (v or "").strip()
    if not v:
        return None
    return v if "://" in v else "http://" + v


HTTP_PROXY  = _normalize_proxy(_get("HTTP_PROXY"))
HTTPS_PROXY = _normalize_proxy(_get("HTTPS_PROXY"))
USE_PROXY   = bool(HTTP_PROXY or HTTPS_PROXY)
SSL_VERIFY  = not USE_PROXY   # 프록시(사내 SSL 인스펙션) 경유 시 verify=False


def proxies() -> dict | None:
    """requests용 proxies dict. 비활성 시 None."""
    if not USE_PROXY:
        return None
    return {"http": HTTP_PROXY or HTTPS_PROXY, "https": HTTPS_PROXY or HTTP_PROXY}


# ── 기상청 ──────────────────────────────────────────────
WEATHER_API_KEY  = _get("WEATHER_API_KEY")
# 기상청 API허브(authKey) 기준. data.go.kr(serviceKey) 쓰려면 .env에서 WEATHER_BASE_URL 재설정.
WEATHER_BASE_URL = _get("WEATHER_BASE_URL",
                        "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0")


if __name__ == "__main__":
    print(f"환경        : {ENV_LABEL} (사내망 고정)")
    print(f"BASE_PATH   : {BASE_PATH}")
    print(f"PARQUET_PATH: {PARQUET_PATH}")
    print(f"UPLOAD_PATH : {UPLOAD_PATH}")
    print(f"USE_PARQUET : {USE_PARQUET}")
    print(f"USE_PROXY   : {USE_PROXY}")
    print(f"db_url set  : {bool(db_url())}")
    print(f"LLM_API_URL : {LLM_API_URL}")
    print(f"OCR_API_URL : {OCR_API_URL}")
