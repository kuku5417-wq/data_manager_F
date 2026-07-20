"""db/auto_sync.py — parquet 저장 완료 후 MSSQL(jsh_*) 순차 자동 전송.

파이프라인: 엑셀 업로드/생성 → parquet 저장(save_parquet_atomic) → **저장된
parquet 파일을 다시 읽어** 운영서버 MSSQL 에 push (전체 교체). 병렬 스레드가
아닌 순차 실행이며, 업로드 내용은 디스크의 parquet 파일과 항상 일치한다.

- best-effort: 전송 실패해도 parquet 저장에는 영향 없음(예외 전파 금지).
- 운영서버 미설정(is_configured=False) 시 휴면 — 어떤 환경에서도 앱이 죽지 않음.
- TABLES 화이트리스트 + 중앙 parquet 폴더 경로 일치 시에만 전송
  (sql/·백업·사용자 정의 parquet 은 제외).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# 최근 자동 전송 결과 (최신순, 프로세스 메모리 내) — UI 표시용
_RESULTS: deque[dict] = deque(maxlen=30)


def push_saved(path: str | Path) -> dict | None:
    """parquet 저장 직후 호출 — 저장된 파일을 읽어 MSSQL 로 순차 전송.

    반환: 전송 결과 dict / 대상 아님·휴면이면 None.
    호출부(parquet_io)가 안전하도록 어떤 예외도 밖으로 내보내지 않는다.
    """
    try:
        import path_config as pc
        from . import connection, sync, tables

        p = Path(path)
        stem = p.stem.lower()
        if stem not in tables.TABLES:
            return None  # 화이트리스트 외(guide/accident/사용자 정의 등)는 대상 아님
        if p.resolve().parent != Path(pc.get_parquet_dir()).resolve():
            return None  # 중앙 parquet 폴더 외(sql/·백업 등) 저장은 제외
        if not connection.is_configured():
            return None  # 운영서버 미설정 → 휴면

        df = pd.read_parquet(p)          # 저장 완료된 parquet 파일 기준 (내용 일치 보장)
        res = sync.push_table(stem, df)  # 순차(동기) 전송 — 전체 교체
        res["time"] = datetime.now().strftime("%m-%d %H:%M:%S")
        _RESULTS.appendleft(res)
        if res.get("ok"):
            logger.info("MSSQL 자동 전송 완료: %s (%s행)", res["table"], res["rows"])
        else:
            logger.warning("MSSQL 자동 전송 실패: %s — %s", res.get("table"), res.get("msg"))
        return res
    except Exception as e:
        logger.warning("MSSQL 자동 전송 실패(무시): %s", e)
        return None


def recent_results() -> list[dict]:
    """최근 자동 전송 결과(최신순) — UI 표시용."""
    return list(_RESULTS)
