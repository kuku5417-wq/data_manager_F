"""path_config.py — 사내망 NAS 경로 판단 (secret/.env 기반).

data_manager_F: 사내망 전용. app_config(사내망 고정)에서 경로를 위임받는다.
(원본의 사외망 로컬 F:\\code\\data 폴백은 제거)
"""
from __future__ import annotations

import re
from pathlib import Path

BASE_DIR    = Path(__file__).resolve().parent        # data_manager_F/
SECRET_DIR  = BASE_DIR.parent / "secret"             # code_N/secret/

# 사내망 고정 config (app_config.py) — 경로 단일 소스.
import sys as _sys
if str(BASE_DIR.parent) not in _sys.path:
    _sys.path.insert(0, str(BASE_DIR.parent))
import app_config as _ac

# secret 파일 — API/DB/프록시/NAS_PATH 설정
SECRET_ENV = SECRET_DIR / ".env"


def _secret_path() -> Path | None:
    try:
        if SECRET_ENV.exists():
            return SECRET_ENV
    except Exception:
        pass
    return None

# 섹션별 업로드 폴더 (upload/ 하위) — NAS/로컬 동일 구조
# esg/ptw/out: 엑셀 업로드, accident/guide: PDF 원본, message: 안전메시지 첨부
UPLOAD_SECTIONS = ("esg", "ptw", "out", "accident", "guide", "message")


def read_secret(key: str) -> str:
    """secret/.env에서 KEY= 변수 읽기"""
    p = _secret_path()
    if p is None:
        return ""
    try:
        text = p.read_text(encoding="utf-8")
        # 값에 개행을 포함하지 않도록 [ \t]/[^\n] 사용 — 빈 값이 다음 줄을 빨아들이는 버그 방지
        m = re.search(rf"^{re.escape(key)}[ \t]*=[ \t]*([^\n]*)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


_ensured: set[Path] = set()


def _ensure(d: Path) -> Path:
    """디렉토리를 프로세스당 1회만 생성 (네트워크 mkdir 반복 제거)."""
    if d not in _ensured:
        d.mkdir(parents=True, exist_ok=True)
        _ensured.add(d)
    return d


def get_parquet_dir() -> Path:
    """최종 parquet 저장 디렉토리 (사내망 NAS)."""
    return _ensure(Path(_ac.PARQUET_PATH))


def get_upload_dir(section: str | None = None) -> Path:
    """섹션별 데이터 업로드 디렉토리 (사내망 NAS).

    upload/esg  — ESG 엑셀 (6개 시트)
    upload/ptw  — TBM ptwlist_YYMMDD.xlsx
    upload/out  — outside_*.xlsx
    section 미지정 시 upload/ 루트 반환.
    """
    base = Path(_ac.UPLOAD_PATH)
    d = base / section if section else base
    return _ensure(d)


def get_backup_dir(section: str) -> Path:
    """원본 Excel 백업 디렉토리 (upload/<section>/_backup)"""
    d = get_upload_dir(section) / "_backup"
    return _ensure(d)


def get_processed_dir(section: str) -> Path:
    """무인 잡이 변환 완료한 원본을 옮겨두는 디렉토리 (upload/<section>/_processed).

    백업(_backup, 타임스탬프 사본)과 별개. 처리 완료 표식 + 폴더 정리 용도로,
    다음 잡 실행 시 같은 파일을 재처리(재LLM·중복저장)하지 않게 한다.
    """
    d = get_upload_dir(section) / "_processed"
    return _ensure(d)


def get_message_dir() -> Path:
    """안전메시지 첨부파일 저장 디렉토리 (upload/message)."""
    return get_upload_dir("message")


def ensure_upload_dirs() -> None:
    """섹션별 업로드 폴더 일괄 생성 (앱 기동 시 호출)"""
    for s in UPLOAD_SECTIONS:
        get_upload_dir(s)


def get_sql_dir() -> Path:
    """SQL 미접속 시 대체 parquet 디렉토리 (사내망 NAS base/sql)."""
    return _ensure(Path(_ac.BASE_PATH) / "sql")


def get_env_label() -> str:
    """현재 환경 레이블 (UI 표시용) — 사내망 고정."""
    return f"🟢 사내망 (NAS: {_ac.NAS_BASE_PATH})"


def touch_sentinel() -> None:
    """ESG parquet 저장 후 호출 → esg_update 캐시 무효화 트리거"""
    from datetime import datetime
    p = get_parquet_dir() / "last_updated.txt"
    try:
        p.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass


def invalidate_cache() -> None:
    """경로 캐시 무효화 (하위호환 no-op — 사내망 고정본은 경로 캐시 없음)."""
    return None
