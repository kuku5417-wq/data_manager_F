"""ptw_enrich.py — ptwlist 위험요소 컬럼(risk_keywords/warning) 보강.

ACODENM(작업허가대상)을 키로 mapping.parquet 조회 → 채움.
미매핑 ACODENM은 LLM 호출로 생성하고 mapping에 누적(캐시).

안정성 결정(5-C):
- LLM 실패해도 저장은 진행(예외 삼킴) → 해당 행은 빈 값
- **빈 LLM 결과는 mapping에 캐시하지 않음** → 다음 업로드에 재시도
- mapping 저장은 **쓰기 직전 재읽기-병합**으로 lost-update 방지(계속 누적)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import path_config as pc
from parquet_io import save_parquet_atomic


def _mapping_path() -> Path:
    return pc.get_parquet_dir() / "mapping.parquet"


def load_mapping() -> dict[str, dict]:
    """mapping.parquet → {work: {'keyword':..., 'warning':...}}."""
    path = _mapping_path()
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        work = str(r.get("work", "")).strip()
        if not work:
            continue
        out[work] = {"keyword": str(r.get("keyword", "") or ""),
                     "warning": str(r.get("warning", "") or "")}
    return out


def _merge_mapping(new_map: dict[str, dict]) -> None:
    """쓰기 직전 최신 mapping 재읽기 → 병합(누적) → 원자적 저장."""
    if not new_map:
        return
    path = _mapping_path()
    if path.exists():
        try:
            base = pd.read_parquet(path)
        except Exception:
            base = pd.DataFrame(columns=["work", "keyword", "warning"])
    else:
        base = pd.DataFrame(columns=["work", "keyword", "warning"])
    add = pd.DataFrame([{"work": w, "keyword": v["keyword"], "warning": v["warning"]}
                        for w, v in new_map.items()])
    merged = (pd.concat([base, add], ignore_index=True)
              .drop_duplicates(subset=["work"], keep="last")
              .reset_index(drop=True))
    save_parquet_atomic(merged, path)


def _llm_for_worktype(wt: str) -> dict | None:
    """단일 작업유형 위험요소 LLM 생성. 실패 시 None."""
    try:
        from llm_client import call_llm
    except Exception:
        return None
    prompt = (
        "조선소 현장에서 수행되는 다음 작업의 안전 위험요소를 분석하세요.\n\n"
        f"작업 유형: {wt}\n\n"
        'JSON 형식으로만 응답:\n'
        '{"keyword": "키워드1, 키워드2, 키워드3", "warning": "현장 작업자용 경고 1~2문장"}'
    )
    try:
        return call_llm(prompt, max_tokens=256)
    except Exception:
        return None


def unmapped_worktypes(df: pd.DataFrame, mapping: dict[str, dict] | None = None) -> list[str]:
    """ptwlist df의 ACODENM 중 mapping에 없는 작업유형 목록 (LLM 호출 없음).

    시뮬레이션 프리뷰·매핑 탭 스캔 공용. 순서 보존, 중복 제거.
    """
    if df is None or df.empty or "ACODENM" not in df.columns:
        return []
    if mapping is None:
        mapping = load_mapping()
    out: list[str] = []
    for wt in df["ACODENM"].tolist():
        if wt is None or (isinstance(wt, float) and pd.isna(wt)):
            continue
        wt = str(wt).strip()
        if wt and wt not in mapping and wt not in out:
            out.append(wt)
    return out


def generate_for_worktypes(types: list[str], progress_cb=None) -> dict:
    """주어진 작업유형들을 LLM으로 생성해 mapping.parquet에 누적 저장.

    Returns: {'ok': int, 'fail': int, 'added': [work...], 'llm_err': str}.
    빈 결과는 저장 안 함(재시도 가능).
    llm_err — 첫 실패의 provider별 사유(SOLA/Upstage/OpenAI). 화면이 "응답 없음"만
    보여주면 키 만료(401)인지 미설정인지 알 수 없어 진단이 불가능하다
    (enrich_ptwlist 와 같은 표면화 규칙).
    progress_cb(done, total, work) 호출(선택).
    """
    types = [t for t in (types or []) if t]
    total = len(types)
    new_map: dict[str, dict] = {}
    fail: list[str] = []
    llm_err = ""
    for i, wt in enumerate(types):
        res = _llm_for_worktype(wt)
        kw   = (res or {}).get("keyword", "") if isinstance(res, dict) else ""
        warn = (res or {}).get("warning", "") if isinstance(res, dict) else ""
        kw, warn = str(kw or "").strip(), str(warn or "").strip()
        if kw:
            new_map[wt] = {"keyword": kw, "warning": warn}
        else:
            fail.append(wt)
            if not llm_err:      # 첫 실패 사유만 보관 (반복 호출로 덮이기 전에)
                try:
                    from llm_client import _LLM_LAST_ERRORS
                    llm_err = " | ".join(_LLM_LAST_ERRORS)
                except Exception:   # noqa: BLE001 — 진단 정보 수집 실패는 무시
                    pass
        if progress_cb:
            try:
                progress_cb(i + 1, total, wt)
            except Exception:
                pass
    _merge_mapping(new_map)
    return {"ok": len(new_map), "fail": len(fail), "added": list(new_map.keys()),
            "llm_err": llm_err}


def enrich_ptwlist(df: pd.DataFrame, use_llm: bool = True) -> tuple[pd.DataFrame, dict]:
    """ptwlist df에 risk_keywords/warning 채워 반환. ACODENM 키 사용.

    Returns: (df, stats). stats = {'unmapped': int, 'llm_ok': int, 'llm_fail': int, 'added': [work...]}.
    """
    stats = {"unmapped": 0, "llm_ok": 0, "llm_fail": 0, "added": []}
    df = df.copy()
    if "risk_keywords" not in df.columns:
        df["risk_keywords"] = None
    if "warning" not in df.columns:
        df["warning"] = None
    if df.empty or "ACODENM" not in df.columns:
        return df, stats

    mapping = load_mapping()

    # 1) 룰베이스(mapping) 적용 + 미매핑 수집
    unmapped: list[str] = []
    for idx, row in df.iterrows():
        wt = row.get("ACODENM")
        if wt is None or (isinstance(wt, float) and pd.isna(wt)):
            continue
        wt = str(wt).strip()
        if not wt:
            continue
        if wt in mapping:
            df.at[idx, "risk_keywords"] = mapping[wt]["keyword"]
            df.at[idx, "warning"]       = mapping[wt]["warning"]
        elif wt not in unmapped:
            unmapped.append(wt)

    stats["unmapped"] = len(unmapped)

    # 2) 미매핑 → LLM (빈값은 캐시 안 함)
    if use_llm and unmapped:
        new_map: dict[str, dict] = {}
        for wt in unmapped:
            res = _llm_for_worktype(wt)
            kw   = (res or {}).get("keyword", "") if isinstance(res, dict) else ""
            warn = (res or {}).get("warning", "") if isinstance(res, dict) else ""
            kw, warn = str(kw or "").strip(), str(warn or "").strip()
            if kw:   # 비어있지 않을 때만 반영·캐시
                mask = df["ACODENM"].astype(str).str.strip() == wt
                df.loc[mask, "risk_keywords"] = kw
                df.loc[mask, "warning"]       = warn
                new_map[wt] = {"keyword": kw, "warning": warn}
            elif not stats.get("llm_err"):   # 첫 실패 사유를 표면화 (숨은 실패 진단)
                try:
                    from llm_client import _LLM_LAST_ERRORS
                    if _LLM_LAST_ERRORS:
                        stats["llm_err"] = " | ".join(_LLM_LAST_ERRORS)
                except Exception:
                    pass
            # 실패/빈값 → 컬럼 None 유지, mapping 미저장 → 다음에 재시도
        _merge_mapping(new_map)
        stats["llm_ok"]   = len(new_map)
        stats["llm_fail"] = len(unmapped) - len(new_map)
        stats["added"]    = list(new_map.keys())

    return df, stats


def regenerate_keywords(parquet_dir: Path) -> dict:
    """기존 ptwlist.parquet → 위험요소(risk_keywords/warning) 재생성·저장 (변환 재실행 없이).

    매핑(룰) 적용 + 미매핑은 LLM 호출. LLM 실패 사유는 llm_err 로 표면화.
    Returns: {ok, total, unmapped, llm_ok, llm_fail, llm_err, msg}
    """
    path = parquet_dir / "ptwlist.parquet"
    if not path.exists():
        return {"ok": False, "total": 0, "msg": "ptwlist.parquet 없음 — 먼저 변환하세요."}
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return {"ok": False, "total": 0, "msg": f"읽기 실패: {e}"}

    df2, stats = enrich_ptwlist(df, use_llm=True)
    try:
        save_parquet_atomic(df2, path)
    except Exception as e:
        return {"ok": False, "total": len(df2), "msg": f"저장 실패: {e}"}

    err = f" · [원인: {stats['llm_err']}]" if stats.get("llm_err") else ""
    msg = (f"미매핑 {stats['unmapped']}종 → LLM {stats['llm_ok']}건 추가"
           f"/{stats['llm_fail']}건 실패{err}" if stats["unmapped"]
           else "미매핑 없음 — 모두 매핑(룰)으로 채움")
    return {"ok": True, "total": len(df2), "unmapped": stats["unmapped"],
            "llm_ok": stats["llm_ok"], "llm_fail": stats["llm_fail"],
            "llm_err": stats.get("llm_err", ""), "msg": msg}
