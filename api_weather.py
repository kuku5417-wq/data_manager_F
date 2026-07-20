"""api_weather.py — 기상청 단기예보 수집 (data_manager 이식본).

원본: tbm_system_v6/modules/api_weather.py
변경: 상수 내장(자립형), secret/경로를 settings·path_config로, 저장 원자적.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import path_config as pc
from settings import secrets
from parquet_io import save_parquet_atomic

# ── 상수 (구 config/settings.py) ─────────────────────────
GRID_COORDS = [{"nx": 90, "ny": 69, "label": "거제시 장평동"}]
WEATHER_RULES = {
    "temp_min_cold":  0,
    "temp_max_hot":   30,
    "rainfall_heavy": 30,
    "wind_strong":    15,
    "wave_high":      1.0,
}
BASE_TIMES = ["0200", "0500", "0800", "1100", "1400", "1700", "2000", "2300"]

_DIRECTIONS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
               "S","SSW","SW","WSW","W","WNW","NW","NNW"]
PTY_MAP = {"0":"없음","1":"비","2":"비/눈","3":"눈","4":"소나기","5":"빗방울","6":"빗방울/눈날림","7":"눈날림"}
SKY_MAP = {"1":"맑음","3":"구름많음","4":"흐림"}

_fetch_last_error: str = ""


def _deg_to_dir(deg: float | None) -> str:
    if deg is None:
        return "-"
    return _DIRECTIONS[round(deg / 22.5) % 16]


def _weather_path() -> Path:
    return pc.get_parquet_dir() / "weather.parquet"


def _get_base_datetime() -> tuple[str, str]:
    now = datetime.now()
    for bt in reversed(BASE_TIMES):
        candidate = now.replace(hour=int(bt[:2]), minute=int(bt[2:]), second=0, microsecond=0)
        if now >= candidate + timedelta(minutes=10):
            return now.strftime("%Y%m%d"), bt
    return (now - timedelta(days=1)).strftime("%Y%m%d"), "2300"


def _mask_key(msg: str, api_key: str) -> str:
    """오류 메시지에 포함될 수 있는 API 키(URL 쿼리 포함)를 ***로 마스킹."""
    if api_key:
        msg = msg.replace(api_key, "***")
    return msg


def fetch_forecast(api_key: str, nx: int, ny: int, num_of_rows: int = 1000) -> list[dict]:
    global _fetch_last_error
    _fetch_last_error = ""
    base_date, base_time = _get_base_datetime()
    params = {
        # 같은 키를 두 이름으로 전송: apihub=authKey, data.go.kr=serviceKey (상대는 무시)
        # → WEATHER_BASE_URL만 바꾸면 두 시스템 다 동작
        "authKey": api_key, "serviceKey": api_key,
        "pageNo": 1, "numOfRows": num_of_rows,
        "dataType": "JSON", "base_date": base_date, "base_time": base_time,
        "nx": nx, "ny": ny,
    }
    url = f"{secrets.weather_base_url}/getVilageFcst"
    try:
        resp = requests.get(url, params=params, proxies=secrets.proxies,
                            timeout=15, verify=secrets.ssl_verify)
        resp.raise_for_status()
        j = resp.json()
        body = j.get("response", j)["body"]   # data.go.kr/apihub 둘 다 response 래퍼 허용
        if body["totalCount"] == 0:
            _fetch_last_error = "API 정상 응답이나 totalCount=0 (발표 시각 전일 가능성)"
            return []
        return body["items"]["item"]
    except requests.exceptions.ProxyError as e:
        _fetch_last_error = _mask_key(f"프록시 오류: {e} — 사내망 프록시(HTTP_PROXY) 설정 확인", api_key)
        return []
    except Exception as e:
        _fetch_last_error = _mask_key(f"{type(e).__name__}: {e}", api_key)
        return []


def parse_forecast(items: list[dict]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    daily: dict[str, dict] = {}
    for it in items:
        dt, cat, val = it["fcstDate"], it["category"], it["fcstValue"]
        d = daily.setdefault(dt, {"tmp_list": [], "tmn": None, "tmx": None,
                                  "pop_list": [], "pty_list": [], "sky_list": [],
                                  "wsd_list": [], "vec_list": [], "wav_list": []})
        try:
            fval = float(val)
        except (ValueError, TypeError):
            fval = None
        if   cat == "TMP" and fval is not None: d["tmp_list"].append(fval)
        elif cat == "TMN" and fval is not None: d["tmn"] = fval
        elif cat == "TMX" and fval is not None: d["tmx"] = fval
        elif cat == "POP" and fval is not None: d["pop_list"].append(fval)
        elif cat == "PTY":                       d["pty_list"].append(val)
        elif cat == "SKY":                       d["sky_list"].append(val)
        elif cat == "WSD" and fval is not None: d["wsd_list"].append(fval)
        elif cat == "VEC" and fval is not None: d["vec_list"].append(fval)
        elif cat == "WAV" and fval is not None: d["wav_list"].append(fval)

    rows = []
    for dt_str, d in sorted(daily.items()):
        tmp_list = d["tmp_list"]
        pty_codes = [c for c in d["pty_list"] if c != "0"]
        pty_code  = max(set(pty_codes), key=pty_codes.count) if pty_codes else "0"
        sky_codes = d["sky_list"]
        sky_code  = max(set(sky_codes), key=sky_codes.count) if sky_codes else "1"
        vec_avg   = sum(d["vec_list"]) / len(d["vec_list"]) if d["vec_list"] else None
        rows.append({
            "forecast_date": datetime.strptime(dt_str, "%Y%m%d").date(),
            "tmp_min": min(tmp_list) if tmp_list else d["tmn"],
            "tmp_max": max(tmp_list) if tmp_list else d["tmx"],
            "tmp_avg": round(sum(tmp_list)/len(tmp_list), 1) if tmp_list else None,
            "pop_max": max(d["pop_list"]) if d["pop_list"] else None,
            "pty_code": pty_code, "pty_label": PTY_MAP.get(pty_code, "-"),
            "sky_code": sky_code, "sky_label": SKY_MAP.get(sky_code, "-"),
            "wsd_avg": round(sum(d["wsd_list"])/len(d["wsd_list"]), 1) if d["wsd_list"] else None,
            "wsd_max": max(d["wsd_list"]) if d["wsd_list"] else None,
            "wav_height": max(d["wav_list"]) if d["wav_list"] else None,
            "vec_deg": round(vec_avg, 1) if vec_avg is not None else None,
            "vec_dir": _deg_to_dir(vec_avg),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "keyword": None, "warning": None,
        })
    return pd.DataFrame(rows)


def apply_rule_based_risk(df: pd.DataFrame) -> pd.DataFrame:
    rules = WEATHER_RULES

    def _analyze(row):
        kw, wn = [], []
        if row["tmp_min"] is not None and row["tmp_min"] <= rules["temp_min_cold"]:
            kw.append("동파위험"); wn.append(f"최저기온 {row['tmp_min']}°C — 블랙아이스·동파 주의")
        if row["tmp_max"] is not None and row["tmp_max"] >= rules["temp_max_hot"]:
            kw.append("온열질환"); wn.append(f"최고기온 {row['tmp_max']}°C — 온열질환 주의")
        if row["pop_max"] is not None and row["pop_max"] >= rules["rainfall_heavy"]:
            kw.append("미끄럼주의"); wn.append(f"강수확률 {row['pop_max']}% — 침수·미끄럼 주의")
        if row["wsd_max"] is not None and row["wsd_max"] >= rules["wind_strong"]:
            kw.append("강풍"); wn.append(f"최대풍속 {row['wsd_max']}m/s — 고소작업 제한")
        if row["wav_height"] is not None and row["wav_height"] >= rules["wave_high"]:
            kw.append("수중작업금지"); wn.append(f"파고 {row['wav_height']}m — 수중작업 중지")
        return (", ".join(kw) if kw else None, " / ".join(wn) if wn else None)

    res = df.apply(_analyze, axis=1)
    df = df.copy()
    df["keyword"] = res.map(lambda x: x[0])
    df["warning"] = res.map(lambda x: x[1])
    return df


def save_weather_parquet(df: pd.DataFrame) -> Path:
    path = _weather_path()
    if path.exists():
        existing = pd.read_parquet(path)
        existing = existing[~existing["forecast_date"].isin(df["forecast_date"])]
        df = pd.concat([existing, df], ignore_index=True).sort_values("forecast_date")
    return save_parquet_atomic(df, path)


def load_weather_parquet() -> pd.DataFrame | None:
    path = _weather_path()
    return pd.read_parquet(path) if path.exists() else None


def collect_all(api_key: str | None = None, progress_cb=None) -> tuple[pd.DataFrame, str]:
    """전체 수집 파이프라인. api_key 미지정 시 .env WEATHER_API_KEY 사용."""
    def _cb(step, msg):
        if progress_cb:
            progress_cb(step, msg)

    api_key = api_key or secrets.weather_api_key
    if not api_key:
        return pd.DataFrame(), "❌ .env WEATHER_API_KEY 미설정"

    _cb(1, "🌐 기상청 API 접속 중...")
    coord = GRID_COORDS[0]
    items = fetch_forecast(api_key, coord["nx"], coord["ny"])
    if not items:
        return pd.DataFrame(), f"❌ API 응답 오류: {_fetch_last_error or '알 수 없음'}"
    _cb(2, f"📡 수신 {len(items)}건 → 파싱 중...")
    df = parse_forecast(items)
    if df.empty:
        return df, "❌ 파싱 결과 없음"
    _cb(3, "⚠️ 룰베이스 위험요소 분석 중...")
    df = apply_rule_based_risk(df)
    _cb(4, "💾 weather.parquet 저장 중...")
    save_weather_parquet(df)
    _cb(5, f"✅ 완료! {len(df)}일치 예보 저장 ({coord['label']})")
    return df, "success"
