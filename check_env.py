"""check_env.py — 사내망 가동 전 종합 점검 (doctor).

각 앱 폴더에서 **로컬 venv python**으로 실행:
    C:\\venvs\\<app>\\Scripts\\python.exe check_env.py
    # 또는
    python check_env.py            # 기본(데이터·DB·프록시·날씨 점검)
    python check_env.py --llm      # + LLM 실제 호출 1회(토큰 소량 사용)
    python check_env.py --quick     # 네트워크 호출 생략(빠른 점검)

리포트: [PASS]/[WARN]/[FAIL]/[INFO] + 조치 힌트. FAIL 있으면 종료코드 1.
값(키/비밀번호)은 출력하지 않고 '설정됨/미설정'만 표시한다.
"""
from __future__ import annotations

import sys
import os
import socket
import importlib
import importlib.util
from pathlib import Path

# 콘솔 인코딩(한글) 안전
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARGS = set(sys.argv[1:])
DO_LLM = "--llm" in ARGS
QUICK = "--quick" in ARGS

_FAILS = 0
_WARNS = 0


def _line(tag: str, msg: str, hint: str = "") -> None:
    global _FAILS, _WARNS
    if tag == "FAIL":
        _FAILS += 1
    elif tag == "WARN":
        _WARNS += 1
    print(f"  [{tag:4}] {msg}")
    if hint:
        print(f"         ↳ {hint}")


def _head(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


def _mask(v: str | None) -> str:
    return "설정됨" if (v and str(v).strip()) else "미설정"


# ══════════════════════════════════════════════════════
# 1. Python / venv
# ══════════════════════════════════════════════════════
def check_python() -> None:
    _head("1. Python / 실행 환경")
    print(f"  python      : {sys.version.split()[0]}")
    print(f"  executable  : {sys.executable}")
    exe = sys.executable.lower()
    # 네트워크 드라이브/UNC 경고 (numpy ImportError 원인)
    drv = os.path.splitdrive(sys.executable)[0]
    is_unc = sys.executable.startswith("\\\\")
    on_c = exe.startswith("c:")
    if is_unc:
        _line("FAIL", "python이 UNC 네트워크 경로에 있음", "로컬 C:\\venvs\\<app> venv로 실행하세요(numpy 오류 회피).")
    elif not on_c:
        _line("WARN", f"python이 {drv} 드라이브(비 C:)에 있음",
              "네트워크 드라이브면 numpy ImportError 가능 → C:\\venvs\\<app> 권장.")
    else:
        _line("PASS", "로컬 C: 디스크 venv")
    # 앱(스크립트) 위치가 네트워크면 streamlit이 UNC로 해석 → numpy 오류
    here = str(Path(__file__).resolve())
    if here.startswith("\\\\"):
        _line("WARN", "앱 폴더가 UNC 네트워크 경로", "가능하면 로컬 디스크에 두거나, venv는 반드시 C: 사용.")


# ══════════════════════════════════════════════════════
# 2. 패키지
# ══════════════════════════════════════════════════════
def check_packages() -> None:
    _head("2. 필수/선택 패키지")
    required = ["streamlit", "pandas", "pyarrow", "openpyxl", "dotenv"]
    # data_manager 가 실제 import 하는 선택 패키지만 점검 (plotly/fastapi 는 이 앱 미사용)
    optional = ["sqlalchemy", "pymysql", "pdfplumber", "openai",
                "holidays", "requests"]
    for name in required:
        spec = importlib.util.find_spec(name)
        if spec:
            ver = _ver(name)
            _line("PASS", f"{name} {ver}")
        else:
            pip = "python-dotenv" if name == "dotenv" else name
            _line("FAIL", f"{name} 미설치", f"uv pip install {pip}")
    for name in optional:
        spec = importlib.util.find_spec(name)
        if spec:
            _line("INFO", f"{name} {_ver(name)}")
        else:
            _line("WARN", f"{name} 미설치(기능별 필요)", f"필요 시 uv pip install {name}")
    # streamlit 버전 힌트(tbm)
    sv = _ver("streamlit")
    if sv and sv.startswith("1.") and sv != "?" :
        try:
            major_minor = float(".".join(sv.split(".")[:2]))
            if major_minor >= 1.40:
                _line("INFO", f"streamlit {sv}", "tbm은 1.35.x 기준(상위 버전은 st.radio 등 호환 주의).")
        except Exception:
            pass


def _ver(name: str) -> str:
    try:
        m = importlib.import_module(name)
        return getattr(m, "__version__", "?")
    except Exception:
        return "?"


# ══════════════════════════════════════════════════════
# 3. app_config / .env
# ══════════════════════════════════════════════════════
def load_app_config():
    _head("3. app_config / .env 단일 소스")
    # app_config는 같은 폴더 또는 상위에 있음
    here = Path(__file__).resolve().parent
    for d in (here, here.parent):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    try:
        ac = importlib.import_module("app_config")
    except Exception as e:
        _line("FAIL", f"app_config import 실패: {e}",
              "app_config.py가 앱 폴더(또는 상위)에 있는지 확인.")
        return None
    print(f"  app_config  : {ac.__file__}")
    # .env 탐색 결과
    envf = ac._find_env_file()
    if Path(envf).exists():
        _line("PASS", f".env 발견: {envf}")
    else:
        _line("FAIL", f".env 없음(탐색값: {envf})",
              "공통 부모폴더 secret\\.env 작성(.env.template 참고). 또는 SHI_ENV_FILE 환경변수 지정.")
    print(f"  환경        : {ac.ENV_LABEL} (사내망 고정)")
    print(f"  BASE_PATH   : {ac.BASE_PATH}")
    print(f"  PARQUET_PATH: {ac.PARQUET_PATH}")
    print(f"  UPLOAD_PATH : {ac.UPLOAD_PATH}")
    print(f"  USE_PROXY   : {ac.USE_PROXY}   SSL_VERIFY: {ac.SSL_VERIFY}   USE_PARQUET: {ac.USE_PARQUET}")
    return ac


# ══════════════════════════════════════════════════════
# 4. .env 키 존재
# ══════════════════════════════════════════════════════
def check_env_keys(ac) -> None:
    _head("4. .env 키 (값은 미표시)")
    # 사내망 전용 필수/권장 키
    internal_keys = ["NAS_BASE_PATH", "DB_MYSQL_HOST", "DB_MYSQL_USER", "DB_MYSQL_PASSWORD",
                     "DB_MYSQL_DATABASE", "LLM_SOLAR_API_KEY", "LLM_SOLAR_API_URL",
                     "HTTP_PROXY", "HTTPS_PROXY"]
    common_keys = ["WEATHER_API_KEY", "DB_SEVER", "DB_NAME", "DB_USERNAME"]  # 기상청 + MSSQL 전송
    for k in internal_keys:
        v = os.getenv(k)
        if v and v.strip():
            _line("PASS", f"{k}: 설정됨")
        else:
            _line("WARN", f"{k}: 미설정", "사내망 기능(NAS/DB/LLM/프록시)에 필요할 수 있음.")
    for k in common_keys:
        _line("INFO" if (os.getenv(k) or "").strip() else "WARN", f"{k}: {_mask(os.getenv(k))}")


# ══════════════════════════════════════════════════════
# 5. 경로 존재
# ══════════════════════════════════════════════════════
def check_paths(ac) -> None:
    _head("5. 경로 접근")
    if ac is None:
        return
    for label, p in [("BASE_PATH", ac.BASE_PATH), ("PARQUET_PATH", ac.PARQUET_PATH),
                     ("UPLOAD_PATH", ac.UPLOAD_PATH)]:
        try:
            if Path(p).exists():
                _line("PASS", f"{label} 접근 OK: {p}")
            else:
                tag = "FAIL" if label == "PARQUET_PATH" else "WARN"
                _line(tag, f"{label} 없음: {p}",
                      "사내망 NAS 경로/마운트 확인 (NAS_BASE_PATH·NAS_*PATH).")
        except Exception as e:
            _line("FAIL", f"{label} 접근 오류: {e}", "권한/마운트 확인.")


# ══════════════════════════════════════════════════════
# 6. 데이터(parquet)
# ══════════════════════════════════════════════════════
def check_data(ac) -> None:
    _head("6. 데이터 parquet (행수/읽기)")
    if ac is None:
        return
    try:
        import pandas as pd
    except Exception:
        _line("FAIL", "pandas 미설치로 데이터 점검 불가", "uv pip install pandas pyarrow")
        return
    pq = Path(ac.PARQUET_PATH)
    expected = ["pjtlist", "milestone", "shipbbs", "trial_schedule", "fuel_usage",
                "fuel_price", "lng_usage", "fuel_plan", "pjtmethod", "ptwlist",
                "out", "ra", "weather", "date", "mapping", "message", "accident", "guide"]
    found = 0
    for key in expected:
        f = pq / f"{key}.parquet"
        if not f.exists():
            _line("WARN", f"{key}.parquet 없음", "data_manager 생산 전이거나 미사용 데이터일 수 있음.")
            continue
        try:
            df = pd.read_parquet(f)
            found += 1
            _line("PASS", f"{key}.parquet : {len(df)}행 x {df.shape[1]}열")
        except Exception as e:
            _line("FAIL", f"{key}.parquet 읽기 실패: {e}", "파일 손상/권한. data_manager에서 재생성.")
    _line("INFO", f"parquet 존재: {found}/{len(expected)}")


# ══════════════════════════════════════════════════════
# 7. MySQL DB
# ══════════════════════════════════════════════════════
def check_db(ac) -> None:
    _head("7. MySQL DB 접속")
    if ac is None:
        return
    url = None
    try:
        url = ac.db_url()
    except Exception as e:
        _line("WARN", f"db_url 계산 오류: {e}")
    if not url:
        _line("INFO", "DB 미사용(parquet 폴백 모드)",
              "사내망에서 DB 직결이 필요하면 .env DB_MYSQL_* 입력 + 사내망 모드 확인.")
        return
    if QUICK:
        _line("INFO", "DB 접속 테스트 생략(--quick)")
        return
    try:
        from sqlalchemy import create_engine, text
    except Exception:
        _line("WARN", "sqlalchemy 미설치로 DB 테스트 생략", "uv pip install sqlalchemy pymysql")
        return
    try:
        eng = create_engine(url, connect_args={"connect_timeout": 6})
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        _line("PASS", "MySQL SELECT 1 성공")
    except Exception as e:
        _line("FAIL", f"MySQL 접속 실패: {type(e).__name__}: {str(e)[:120]}",
              "HOST/PORT/계정/방화벽 확인. 사내망 VPN/망 상태 확인. 실패해도 parquet 폴백으로 가동 가능.")


# ══════════════════════════════════════════════════════
# 8. 프록시 / 외부 연결 / LLM / 날씨
# ══════════════════════════════════════════════════════
def _http(ac, url: str, timeout: int = 6):
    import requests
    proxies = None
    try:
        proxies = ac.proxies()
    except Exception:
        pass
    verify = getattr(ac, "SSL_VERIFY", True)
    return requests.get(url, timeout=timeout, proxies=proxies, verify=verify)


def check_network(ac) -> None:
    _head("8. 외부 연결 (프록시/LLM/날씨)")
    if ac is None or QUICK:
        if QUICK:
            _line("INFO", "네트워크 점검 생략(--quick)")
        return
    if importlib.util.find_spec("requests") is None:
        _line("WARN", "requests 미설치로 네트워크 점검 생략", "uv pip install requests")
        return

    # 프록시 표시
    if ac.USE_PROXY:
        _line("INFO", f"프록시 사용: {_mask(ac.HTTP_PROXY)} (HTTP) / {_mask(ac.HTTPS_PROXY)} (HTTPS), SSL_VERIFY={ac.SSL_VERIFY}")
    else:
        _line("INFO", "프록시 미사용(직접 연결)")

    # 사내 LLM(SOLA) — 사내망 전용
    url, key = ac.LLM_SOLAR_API_URL, ac.LLM_SOLAR_API_KEY
    name = "사내 LLM(SOLAR)"
    if not key:
        _line("WARN", f"{name} 키 미설정", "필요 시 .env에 키 입력.")
    if url:
        _reach(ac, name, url)
    else:
        _line("WARN", f"{name} URL 미설정")

    # 실제 LLM 호출(옵션)
    if DO_LLM and key:
        _live_llm(ac)

    # 기상청 API
    wkey = ac.WEATHER_API_KEY
    wurl = ac.WEATHER_BASE_URL
    if not wkey:
        _line("WARN", "WEATHER_API_KEY 미설정", "기상청 키 입력 필요(날씨 수집).")
    if wurl:
        _reach(ac, "기상청 API", wurl.split("?")[0])


def _reach(ac, name: str, url: str) -> None:
    try:
        r = _http(ac, url, timeout=6)
        # 인증/405 등도 '연결됨'으로 간주 (엔드포인트 도달 확인 목적)
        _line("PASS", f"{name} 연결 OK (HTTP {r.status_code}) — {url}")
    except Exception as e:
        en = type(e).__name__
        hint = "프록시 설정(HTTP_PROXY/HTTPS_PROXY)·방화벽·SSL 확인."
        if "Proxy" in en or "proxy" in str(e).lower():
            hint = "프록시 주소/포트 확인(.env HTTP_PROXY/HTTPS_PROXY)."
        elif "SSL" in en or "Certificate" in str(e):
            hint = "사내 SSL Inspection → SSL_VERIFY=False(프록시 사용 시 자동). 인증서 확인."
        _line("FAIL", f"{name} 연결 실패: {en}: {str(e)[:100]}", hint)


def _live_llm(ac) -> None:
    """--llm: 앱의 llm_client로 1회 호출(가능한 앱만)."""
    try:
        lc = importlib.import_module("llm_client")
    except Exception:
        _line("INFO", "llm_client 모듈 없음 → 실제 LLM 호출 생략(reachability만).")
        return
    try:
        fn = getattr(lc, "call_llm", None)
        if fn is None:
            _line("INFO", "llm_client.call_llm 없음 → 생략")
            return
        out = fn("핑. '오케이'라고만 답하세요.", max_tokens=8)
        ok = bool(out)
        _line("PASS" if ok else "WARN", f"LLM 실제 호출 결과: {str(out)[:40]!r}")
    except Exception as e:
        _line("FAIL", f"LLM 호출 실패: {type(e).__name__}: {str(e)[:100]}",
              "키/프록시/엔드포인트 확인. 사내 SOLA 망 상태 확인.")


# ══════════════════════════════════════════════════════
def main() -> int:
    print("\n" + "#" * 64)
    print("#  사내망 가동 전 점검 (check_env.py)")
    print(f"#  cwd: {os.getcwd()}")
    print("#" * 64)
    check_python()
    check_packages()
    ac = load_app_config()
    check_env_keys(ac)
    check_paths(ac)
    check_data(ac)
    check_db(ac)
    check_network(ac)

    _head("요약")
    if _FAILS == 0 and _WARNS == 0:
        print("  [PASS] 모든 점검 통과 — 가동 가능.")
    elif _FAILS == 0:
        print(f"  [WARN] FAIL 0, WARN {_WARNS} — 가동 가능하나 위 WARN 확인 권장.")
    else:
        print(f"  [FAIL] FAIL {_FAILS}, WARN {_WARNS} — 위 [FAIL] 항목 조치 후 재실행.")
    print("  팁: 'python check_env.py --llm'(실LLM), '--quick'(네트워크 생략).")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
