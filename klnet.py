# -*- coding: utf-8 -*-
"""KL-NET eTrans3.0 컨테이너이동현황(국내) 조회 모듈.

berth 모니터에서 컨테이너 번호로 전 터미널 통합 이동이력을 조회한다.

분석 출처(2026-06-25):
  - 로그인 : POST /login/loginCheckCrypto.do
            바디 {"dma_loginCheck":{USER_ID(대문자), PASSWORD:"", ENC_PASSWORD}}
            ENC_PASSWORD = AES-128-CBC/PKCS7/Base64( PASSWORD.대문자 )  (crypto-js 호환)
  - 조회   : POST /crg/getCntrMoveStatusList.do   (메뉴 /ui/crg/moveMent.xml)
            바디 {"dm_search":{FROM_DT,TO_DT,CONTAINER_NO,PAGE_INDEX,PAGE_SIZE,PAGE_YN}}
            응답 {"page":..,"rsMsg":{statusCode},"dl_moveList":[...]}
  - 계정   : 환경변수 KLNET_USER_ID / KLNET_PASSWORD

⚠️ 비밀번호 5회 연속 오류 시 계정 정지. 자격증명이 정확할 때만 사용.
"""
import os
import json
import base64
import threading
from datetime import datetime

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

BASE = "https://etrans.klnet.co.kr"
LOGIN_URL = BASE + "/login/loginCheckCrypto.do"
QUERY_URL = BASE + "/crg/getCntrMoveStatusList.do"

# 페이지 JS(commonScope.js)에서 추출한 crypto-js AES 키/IV
_AES_KEY = b"7qfQk9ruvmq7Ks88"
_AES_IV = b"LqZollGwLvK4SUxX"

# 적공 코드 (moveMent.xml FULL_EMPTY select)
_FULL_EMPTY = {"5": "적(F)", "4": "공(E)"}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 세션 캐시(로그인 1회 후 재사용). 만료 감지 시 재로그인.
_session = None
_lock = threading.Lock()


def _encrypt_aes(plaintext: str) -> str:
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(ct).decode("ascii")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/main.do",
        "Origin": BASE,
    })
    return s


def _login(s: requests.Session) -> bool:
    uid = os.environ.get("KLNET_USER_ID")
    pwd = os.environ.get("KLNET_PASSWORD")
    if not uid or not pwd:
        raise RuntimeError("환경변수 KLNET_USER_ID / KLNET_PASSWORD 가 필요합니다.")
    s.get(BASE + "/main.do", timeout=15)
    payload = {"dma_loginCheck": {
        "USER_ID": uid.upper(),
        "PASSWORD": "",
        "ENC_PASSWORD": _encrypt_aes(pwd.upper()),   # ID/PW 둘 다 대문자
    }}
    r = s.post(LOGIN_URL, data=json.dumps(payload),
               headers={"Content-Type": "application/json; charset=UTF-8"},
               timeout=15)
    code = (r.json().get("rsMsg") or {}).get("statusCode")
    return code == "S"


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                s = _new_session()
                if not _login(s):
                    raise RuntimeError("KL-NET 로그인 실패 (자격증명 확인)")
                _session = s
    return _session


def _fmt_time(v: str) -> str:
    """YYYYMMDDHHMM -> 'YYYY-MM-DD HH:MM'."""
    if not v:
        return ""
    try:
        if len(v) >= 12:
            return datetime.strptime(v[:12], "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M")
        if len(v) >= 8:
            return datetime.strptime(v[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        pass
    return v


def _map_row(r: dict) -> dict:
    """원시 dl_moveList 레코드를 화면용으로 정리."""
    return {
        "inout": r.get("INOUT") or "",            # 수입/수출
        "move_type": r.get("MOVE_TYPE") or "",    # 반입/반출 등
        "terminal": r.get("TERMINAL") or "",
        "terminal_name": r.get("TERMINAL_NAME") or "",
        "move_time": _fmt_time(r.get("IN_OUT_TIME") or ""),
        "vessel": r.get("VESSEL_CODE") or "",     # 모선
        "voyage": r.get("TERMINAL_REF_NO") or "",  # 항차
        "carrier": r.get("CARRIER_CODE") or "",   # 선사
        "full_empty": _FULL_EMPTY.get(str(r.get("FULL_EMPTY")), r.get("FULL_EMPTY") or ""),
        "size": r.get("TYPE_SIZE") or "",
        "move_from": r.get("MOVE_FROM") or "",     # POD(출발지)
        "move_to": r.get("MOVE_TO") or "",         # POL(도착지)
        "car_no": r.get("CAR_NO") or "",          # 차량번호
        "rfid": r.get("RFID") or "",
        "container_no": r.get("CONTAINER_NO") or "",
        "_raw_time": r.get("IN_OUT_TIME") or "",   # 정렬용
    }


def query_container(container_no: str, from_dt: str = None, to_dt: str = None,
                    page_size: int = 100):
    """컨테이너 번호로 이동이력 조회.

    Returns: {"ok":bool, "message":str, "count":int, "rows":[...]}
    날짜 미지정 시 2000년~오늘 전체.
    """
    container_no = (container_no or "").strip().upper()
    if not container_no:
        return {"ok": False, "message": "컨테이너 번호를 입력하세요.", "count": 0, "rows": []}

    today = datetime.now().strftime("%Y%m%d")
    from_dt = from_dt or ("2000" + today[4:8])
    to_dt = to_dt or today

    def _do(sess):
        payload = {"dm_search": {
            "FROM_DT": from_dt, "TO_DT": to_dt,
            "CONTAINER_NO": container_no,
            "PAGE_INDEX": 1, "PAGE_SIZE": page_size, "PAGE_YN": "Y",
        }}
        return sess.post(QUERY_URL, data=json.dumps(payload),
                         headers={"Content-Type": "application/json; charset=UTF-8"},
                         timeout=20)

    global _session
    s = _get_session()
    r = _do(s)
    body = r.json()
    code = (body.get("rsMsg") or {}).get("statusCode")

    # 세션 만료 등으로 실패하면 1회 재로그인 후 재시도
    if code not in ("S",) and body.get("dl_moveList") is None:
        with _lock:
            _session = None
        s = _get_session()
        body = _do(s).json()
        code = (body.get("rsMsg") or {}).get("statusCode")

    rows = [_map_row(x) for x in (body.get("dl_moveList") or [])]
    rows.sort(key=lambda x: x["_raw_time"], reverse=True)   # 최신순
    return {
        "ok": code == "S",
        "message": (body.get("rsMsg") or {}).get("message") or "",
        "count": (body.get("page") or {}).get("DATA_COUNT", len(rows)),
        "rows": rows,
    }


if __name__ == "__main__":
    import sys
    cno = sys.argv[1] if len(sys.argv) > 1 else "TEMU1234567"
    res = query_container(cno)
    print(json.dumps(res, ensure_ascii=False, indent=2))
