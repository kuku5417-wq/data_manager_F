"""table_actions.py — 카탈로그 테이블 단위 변환 (버튼 딸깍 1회)

catalog_view 의 목록 행·상세 화면에서 호출한다. 새 변환 로직은 두지 않고,
기존 converter/생성기 함수를 테이블 키로 디스패치하기만 한다.

여기서 다루는 것은 **"원본 → 그 테이블 하나"인 변환뿐**이다(RUN_SINGLE).
원본 1개에서 여러 parquet 이 함께 나오는 파이프라인은 대상이 아니며(RUN_MENU),
catalog_view 가 기존 "데이터 변환" 화면의 해당 섹션으로 유도한다:
  - ESG  : xlsx 1개 → 6종 동시 (테이블별로 쪼개면 DRM Excel 재읽기가 6배 +
           6종의 갱신 시점이 어긋나 다운스트림 조인 시 호선별 버전이 섞임)
  - out  : out_converter.convert_and_save 가 out+ra 를 항상 함께 씀

주의:
  - **prune_uploads(원본 파일 삭제)를 절대 호출하지 않는다.** 기존 "폴더 전체 변환"
    버튼 전용이다. 테이블 버튼에서 부르면 누를 때마다 원본이 지워진다.
  - ra 는 regenerate_ra(out.parquet 에서 파생만) 를 쓴다. convert_and_save 를 쓰면
    ra 버튼이 out 까지 덮어쓴다.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

import path_config as pc

# 결과 dict 형식 (converter 들과 동일): {"name", "ok", "rows", "msg"}
Result = dict


# ── 폴더 스캔 (app.py 에서 이관 — 기존 호출부가 alias import 로 재사용) ────────
def list_dir_files(d, pats=None) -> list[str]:
    """폴더의 파일 '이름'만 수집 — os.listdir 사용(파일 stat/내용 미접근, DRM 안전).
    '_' 접두(_backup/_processed 등) 제외. pats 지정 시 이름 패턴(대소문자 무시) 매칭."""
    if d is None:
        return []
    try:
        names = [n for n in os.listdir(d) if not n.startswith("_")]
    except Exception:
        return []
    if pats:
        pats = [pats] if isinstance(pats, str) else list(pats)
        names = [n for n in names if any(fnmatch.fnmatch(n.lower(), p.lower()) for p in pats)]
    return names


def scan_folder(folder_path, pattern) -> list[Path]:
    """pattern: str 또는 list[str]. os.listdir 이름 매칭 — 디렉토리 stat 비의존(DRM/네트워크 안전)."""
    base = Path(folder_path)
    names = list_dir_files(folder_path, pattern)   # str/list 패턴 모두 처리, '_'접두 제외
    return sorted(base / n for n in names)


# ── 테이블별 핸들러 ──────────────────────────────────────────────────────────
# converter import 는 함수 안에서 lazy — 미설치/DB 미연결 환경에서도 앱이 뜨도록.

def _db_gen(name: str) -> list[Result]:
    """MySQL → parquet 개별 생성 (pjtlist/milestone/shipbbs)."""
    import db_connector
    fn = {"pjtlist": db_connector.gen_pjtlist,
          "milestone": db_connector.gen_milestone,
          "shipbbs": db_connector.gen_shipbbs}[name]
    df, msg = fn()
    # gen_* 는 DB 미연결·실패 시 폴백(기존/더미)하고 "⚠️" 로 시작하는 msg 를 준다.
    return [{"name": name, "ok": msg.startswith("✅"), "rows": len(df), "msg": msg}]


def _run_pjtlist() -> list[Result]:
    return _db_gen("pjtlist")


def _run_milestone() -> list[Result]:
    return _db_gen("milestone")


def _run_shipbbs() -> list[Result]:
    return _db_gen("shipbbs")


def _run_weather() -> list[Result]:
    import api_weather
    df, status = api_weather.collect_all()
    ok = status == "success"
    return [{"name": "weather", "ok": ok, "rows": len(df),
             "msg": f"{len(df)}일치 저장 완료" if ok else status}]


def _run_date() -> list[Result]:
    import date_manager
    df = date_manager.generate_date_table()
    date_manager.save_date_parquet(df)
    return [{"name": "date", "ok": True, "rows": len(df),
             "msg": f"date.parquet {len(df)}행 생성"}]


def _run_mapping() -> list[Result]:
    """ptwlist 의 미매핑 작업유형만 LLM 으로 생성해 mapping.parquet 에 누적."""
    import pandas as pd
    import ptw_enrich
    p = pc.get_parquet_dir() / "ptwlist.parquet"
    ptw_df = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    missing = ptw_enrich.unmapped_worktypes(ptw_df)
    if not missing:
        return [{"name": "mapping", "ok": True, "rows": 0,
                 "msg": "미매핑 작업유형 없음 — 갱신할 항목이 없습니다."}]
    res = ptw_enrich.generate_for_worktypes(missing)
    fail_note = f" · {res['fail']}종 실패(재시도 가능)" if res["fail"] else ""
    added = ", ".join(res["added"][:10]) + ("…" if len(res["added"]) > 10 else "")
    return [{"name": "mapping", "ok": res["ok"] > 0, "rows": res["ok"],
             "msg": (f"{res['ok']}종 생성·매핑 추가{fail_note}" + (f" — {added}" if added else ""))
                    if res["ok"] else f"{len(missing)}종 전부 생성 실패 (LLM 응답 없음){fail_note}"}]


def _run_ra() -> list[Result]:
    """out.parquet → ra.parquet 파생만. out 은 건드리지 않는다."""
    import out_converter
    r = out_converter.regenerate_ra(pc.get_parquet_dir())
    return [{"name": "ra", "ok": bool(r.get("ok")), "rows": r.get("rows", 0),
             "msg": r.get("msg", "")}]


def _run_ptwlist() -> list[Result]:
    """upload/ptw 의 ptwlist_*.xlsx 전체 → ptwlist.parquet 누적 (dedup + LLM 위험요소 보강).

    폴더 전체 변환 버튼과 달리 prune_uploads(원본 삭제)를 하지 않는다.
    """
    import pandas as pd
    import tbm_converter
    folder = pc.get_upload_dir("ptw")
    files = scan_folder(folder, "*ptwlist*.xlsx")
    if not files:
        return [{"name": "ptwlist", "ok": False, "rows": 0,
                 "msg": f"변환할 ptwlist 파일이 없습니다 — 폴더/파일명 확인: {folder}"}]
    pq = pc.get_parquet_dir()
    results: list[Result] = []
    ok_n = fail_n = 0
    for f in files:
        try:
            res = tbm_converter.convert_path(f, pq)
        except Exception as e:
            fail_n += 1
            results.append({"name": f.name, "ok": False, "rows": 0, "msg": f"실패 — {e}"})
            continue
        ok = any(r.get("ok") for r in res)
        ok_n, fail_n = (ok_n + 1, fail_n) if ok else (ok_n, fail_n + 1)
        last = res[-1] if res else {}
        results.append({"name": f.name, "ok": ok, "rows": last.get("rows", 0),
                        "msg": last.get("msg", "")})
    try:
        p = pq / "ptwlist.parquet"
        total = len(pd.read_parquet(p)) if p.exists() else 0
    except Exception:
        total = 0
    results.append({"name": "ptwlist", "ok": ok_n > 0 and fail_n == 0, "rows": total,
                    "msg": f"파일 성공 {ok_n} / 실패 {fail_n} · ptwlist.parquet 총 {total:,}행"})
    return results


def _run_esg() -> list[Result]:
    """upload/esg 의 모든 xlsx → ESG parquet 6종 갱신 (파일별 1회 읽기 → 6종 병합).

    원본 1개에서 6종이 함께 나오므로 파일 단위로 convert_path 호출(테이블별로 쪼개지 않음).
    폴더 전체 변환 버튼과 달리 prune_uploads(원본 삭제)를 하지 않는다.
    """
    import esg_converter
    folder = pc.get_upload_dir("esg")
    files = scan_folder(folder, "*.xlsx")
    if not files:
        return [{"name": "esg", "ok": False, "rows": 0,
                 "msg": f"변환할 ESG xlsx 가 없습니다 — 폴더 확인: {folder}"}]
    pq, up = pc.get_parquet_dir(), pc.get_upload_dir("esg")
    ok_n = fail_n = 0
    results: list[Result] = []
    for f in files:
        try:
            res = esg_converter.convert_path(f, pq, up)
            ok = any(r.get("ok") for r in res)
            ok_n, fail_n = (ok_n + 1, fail_n) if ok else (ok_n, fail_n + 1)
            results.append({"name": f.name, "ok": ok, "rows": 0, "msg": ""})
        except Exception as e:
            fail_n += 1
            results.append({"name": f.name, "ok": False, "rows": 0, "msg": f"실패 — {e}"})
    results.append({"name": "esg", "ok": ok_n > 0 and fail_n == 0, "rows": 0,
                    "msg": f"ESG 파일 성공 {ok_n} / 실패 {fail_n} · 6종 parquet 갱신"})
    return results


def _run_out() -> list[Result]:
    """upload/out 의 outside_*.xlsx 전체 → out.parquet + ra.parquet 함께 갱신.

    폴더 전체 변환 버튼과 달리 prune_uploads(원본 삭제)를 하지 않는다.
    """
    import out_converter
    folder = pc.get_upload_dir("out")
    files = scan_folder(folder, out_converter.OUT_FILE_GLOBS)
    if not files:
        return [{"name": "out", "ok": False, "rows": 0,
                 "msg": f"변환할 out 파일이 없습니다 — 폴더/파일명 확인: {folder}"}]
    pq, up = pc.get_parquet_dir(), pc.get_upload_dir("out")
    try:
        files_bytes = [(f.name, out_converter.read_out_source_bytes(f)) for f in files]
        res = out_converter.convert_and_save(files_bytes, pq, up,
                                             existing_ra_path=pq / "ra.parquet")
    except Exception as e:
        return [{"name": "out", "ok": False, "rows": 0, "msg": f"실패 — {e}"}]
    return [r for r in res if r.get("name") in ("out", "ra")] or res


# ── 레지스트리 (catalog_view 가 import — 키 목록의 단일 소스) ─────────────────
# key → {label: 버튼 라벨, help: 툴팁, fn: 핸들러}
RUN_SINGLE: dict[str, dict] = {
    "ptwlist": {
        "label": "📥 ptwlist 변환",
        "help": "upload/ptw 의 ptwlist_*.xlsx 전체 → 일별 확장·dedup 누적 + LLM 위험요소 보강. "
                "원본 파일은 삭제하지 않습니다.",
        "fn": _run_ptwlist,
    },
    "ra": {
        "label": "🔄 ra 재파생",
        "help": "out.parquet → ra.parquet 파생 (out.parquet 은 건드리지 않음). ra_done 등 소비앱 상태 복원.",
        "fn": _run_ra,
    },
    "pjtlist": {
        "label": "🗄️ pjtlist 재생성",
        "help": "MySQL shipinfo → pjtlist.parquet. DB 미연결 시 기존/더미 폴백.",
        "fn": _run_pjtlist,
    },
    "milestone": {
        "label": "🗄️ milestone 재생성",
        "help": "MySQL pjtevnt → milestone.parquet (raw wide). DB 미연결 시 기존/더미 폴백.",
        "fn": _run_milestone,
    },
    "shipbbs": {
        "label": "🗄️ shipbbs 재생성",
        "help": "MySQL shipbbs → shipbbs.parquet. DB 미연결 시 기존 폴백.",
        "fn": _run_shipbbs,
    },
    "weather": {
        "label": "🌐 날씨 수집",
        "help": "기상청 단기예보 API → weather.parquet. .env WEATHER_API_KEY 필요.",
        "fn": _run_weather,
    },
    "date": {
        "label": "📅 달력 생성",
        "help": "2025-01-01 ~ 오늘+7일, 공휴일 포함 → date.parquet.",
        "fn": _run_date,
    },
    "mapping": {
        "label": "🤖 미매핑 LLM 생성",
        "help": "ptwlist 의 미매핑 작업유형만 LLM 으로 생성해 mapping.parquet 에 누적.",
        "fn": _run_mapping,
    },
    # ESG 6종 — 원본 1개 → 6종 동시. 어느 상세에서 눌러도 폴더 전체를 1회 읽어 6종 갱신.
    **{k: {
        "label": "📥 ESG 원본 전체 변환 (6종 갱신)",
        "help": "upload/esg 의 xlsx 전체를 읽어(win32/DRM 복호) 6종 parquet 병합 갱신. 원본 미삭제.",
        "fn": _run_esg,
    } for k in ("trial_schedule", "fuel_usage", "fuel_price", "lng_usage", "fuel_plan", "pjtmethod")},
    "out": {
        "label": "📥 out+ra 변환",
        "help": "upload/out 의 outside 파일 전체 → out.parquet + ra.parquet 함께 갱신. 원본 미삭제.",
        "fn": _run_out,
    },
}

# 원본 1개 → parquet 여러 개인 변환도 상세에서 인라인 실행하도록 전부 RUN_SINGLE 로 이관.
# (변환 화면 유도용 RUN_MENU 는 비움 — 링크 대신 상세 버튼으로 통일)
RUN_MENU: dict[str, str] = {}
MENU_REASON: dict[str, str] = {}


def run(key: str) -> list[Result]:
    """테이블 단위 변환 실행. 성공 항목이 하나라도 있으면 센티널 갱신.

    어떤 실패도 예외로 새어나가지 않는다(앱이 죽으면 안 됨).
    Returns: [{"name", "ok", "rows", "msg"}, ...]
    """
    entry = RUN_SINGLE.get(key)
    if entry is None:
        return [{"name": key, "ok": False, "rows": 0, "msg": f"테이블 단위 변환 대상이 아닙니다: {key}"}]
    try:
        results = entry["fn"]()
    except Exception as e:
        return [{"name": key, "ok": False, "rows": 0, "msg": f"실패 — {type(e).__name__}: {e}"}]
    if any(r.get("ok") for r in results):
        try:
            pc.touch_sentinel()   # 다운스트림 캐시 무효화
        except Exception:
            pass
    return results
