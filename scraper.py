# -*- coding: utf-8 -*-
"""터미널 선석배정(접안) 스케줄 스크래퍼.

각 터미널의 데이터를 가져와 공통 정규화 레코드 리스트로 반환한다.

정규화 레코드 스키마:
    {
        "terminal": "HJNC",          # 터미널 코드
        "voyage": "OZEL-0002",       # 모선항차 (변동추적 키)
        "vessel": "ZENITH LUMOS",    # 선박명
        "route": "FE4",              # 항로
        "operator": "ONE",           # 운항선사
        "etb": "2026-06-16 19:00",   # 접안(예정)시간
        "etd": "2026-06-20 10:00",   # 출항(예정)시간
        "berth": "40-64",            # 선석(비트)
        "work": "3535/5251/258/9044",# 양하/적하/이적/합계
        "callsign": "5LOS7",
        "rotation": "KRPUS-CNSHA-...",
        "raw": { ... }               # 원본 라벨->값 전체 (디버깅/확장용)
    }
"""
import datetime as dt
import os

import requests
from bs4 import BeautifulSoup

import config

# 클라우드(Render) 여부 — Render는 RENDER=true 환경변수를 자동 주입.
ON_CLOUD = bool(os.environ.get("RENDER"))

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 25


def _make_session(terminal):
    """요청 세션 생성. use_cloudscraper=True면 Cloudflare 우회 세션 사용
    (클라우드 데이터센터 IP에서 PNC 등 Cloudflare 403 대응). 미설치 시 일반 세션."""
    if terminal.get("use_cloudscraper"):
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False})
            s.headers.update({"Accept-Language": HEADERS["Accept-Language"]})
            return s
        except Exception:  # noqa: BLE001
            pass
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _today_range():
    today = dt.date.today()
    start = today - dt.timedelta(days=config.LOOKBACK_DAYS)
    end = today + dt.timedelta(days=config.LOOKAHEAD_DAYS)
    return start, end


def _clean(s):
    return " ".join((s or "").split()).strip()


def _norm_dt(s):
    """'2026/06/17 07:12' / '2026-06-17 07:12' -> '2026-06-17 07:12'."""
    s = _clean(s).replace("/", "-")
    return s


def _pairs(block):
    """블록 내 (라벨, 값) 쌍 추출. 라벨은 th 또는 첫 td, 값은 그 다음 셀."""
    out = []
    for tr in block.select("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            label = _clean(cells[0].get_text(" ", strip=True))
            value = _clean(cells[1].get_text(" ", strip=True))
            if label:
                out.append((label, value))
    return out


def _pick(pairs, *keywords):
    """라벨에 keyword 중 하나라도 포함된 첫 값 반환."""
    for label, value in pairs:
        for kw in keywords:
            if kw in label:
                return value, label
    return "", ""


# ---------------------------------------------------------------------------
# 공통 eSvc 플랫폼 파서 (HJNC / BNCT / DGT ...)
#   두 방언 지원:
#   - HJNC형 : 라벨 td, '선박명/모선항차/항로/접안(예정)시간/From-To비트'
#   - BNCT형 : 라벨 th, '모선명/모선항차(선사항차)/선석·ROUTE/Bitt No/접안시간'
# ---------------------------------------------------------------------------
def parse_esvc_g(terminal):
    url = terminal["base_url"].rstrip("/") + "/esvc/vessel/berthScheduleG"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    records = []
    seen = set()
    for block in soup.select("div.berth-schedule"):
        pairs = _pairs(block)
        raw = {label: value for label, value in pairs}

        voyage_raw, _ = _pick(pairs, "모선항차")
        if not voyage_raw:
            continue
        voyage_key = voyage_raw.split("(")[0].strip()  # 선사항차/상태표시 제거

        vessel, _ = _pick(pairs, "선박명", "모선명")

        # 선석: Bitt No 우선, 없으면 '선석/From-To비트' 첫 토큰
        bitt, _ = _pick(pairs, "Bitt", "비트")
        berth_field, blabel = _pick(pairs, "선석")
        if "/" in berth_field:
            berth_field = berth_field.split("/")[0].strip()
        berth = bitt or berth_field
        if "/" in berth:  # 'From-To비트/선교' 등 뒤쪽 선교 제거
            berth = berth.split("/")[0].strip()

        # 항로: '항로' 라벨 우선, 없으면 ROUTE 포함 라벨의 마지막 '/' 뒤
        route, _ = _pick(pairs, "항로")
        if not route:
            rval, rlabel = _pick(pairs, "ROUTE", "Route")
            route = rval.split("/")[-1].strip() if rval else ""

        # 현재 배='접안시간', 미래 배='접안예정시간' 둘 다 흡수('접안형'은 제외)
        etb, _ = _pick(pairs, "접안시간", "접안예정", "접안(예정)")
        etd, _ = _pick(pairs, "출항시간", "출항예정", "출항(예정)")
        cct, _ = _pick(pairs, "반입마감")
        work, _ = _pick(pairs, "양하", "양적하")
        operator, _ = _pick(pairs, "운항선사", "선사명")
        callsign, _ = _pick(pairs, "호출부호", "Call")
        rotation, _ = _pick(pairs, "Rotation", "기항")

        dedup = (voyage_key, vessel)
        if dedup in seen:
            continue
        seen.add(dedup)

        records.append({
            "terminal": terminal["code"],
            "voyage": voyage_key,
            "vessel": vessel,
            "route": route,
            "operator": operator,
            "cct": _norm_dt(cct),
            "etb": _norm_dt(etb),
            "etd": _norm_dt(etd),
            "berth": berth,
            "work": work,
            "callsign": callsign,
            "rotation": rotation,
            "raw": raw,
        })
    return records


# ---------------------------------------------------------------------------
# 범용 표 파서 (헤더 키워드 매핑)
#   PNIT/HPNT(infoservice/vessel/vslScheduleList.jsp), PNC(Info.pnc) 등
#   '헤더가 있는 표' 형태를 컬럼 순서와 무관하게 흡수한다.
# ---------------------------------------------------------------------------
def _match_field(header):
    """헤더 셀 텍스트 -> 정규화 필드명(없으면 None).

    여러 터미널의 헤더 방언을 흡수:
      접안일시: '접안(예정)일시'(신항) / '입항 예정일시'(BPT북항)
      선박명  : '선명'(신항) / '선박명'(BPT)
      구분    : 감만/신선대 등 서브존(BPT) -> berth 앞에 결합
    """
    h = header.strip()
    hu = h.upper()
    # 반입마감(CCT) — '반입마감일시/시한' / '반입 마감일시'. '반입시작'은 제외.
    if "반입" in h and "마감" in h:
        return "cct"
    if "모선항차" in h or "모선코드" in h:
        return "voyage"
    if "선박명" in h or "선명" in h or "모선명" in h:
        return "vessel"
    if "운항선사" in h or h == "선사":
        return "operator"
    if "ROUTE" in hu or "항로" in h:
        return "route"
    # ETB: 시간을 가진 접안/입항예정 컬럼만 (방향 'S/P' 단독 '접안' 컬럼 제외)
    if ("접안" in h and ("일시" in h or "예정" in h)) or ("입항" in h and "예정" in h):
        return "etb"
    if "출항" in h:
        return "etd"
    if h == "구분":
        return "zone"
    if h == "선석" or ("선석" in h and "항차" not in h):
        return "berth"
    if "양하" in h:
        return "discharge"
    if "적하" in h or "선적" in h:
        return "load"
    if "호출" in h or "CALL" in hu:
        return "callsign"
    return None


def _best_table(soup):
    """voyage/etb 컬럼을 가진 데이터 표 중 행이 가장 많은 것을 선택."""
    best, best_rows = None, 0
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 3:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        fields = [_match_field(_clean(c.get_text(" ", strip=True))) for c in header_cells]
        if "voyage" in fields and "etb" in fields and len(rows) > best_rows:
            best, best_rows = (t, fields), len(rows)
    return best


def parse_table(terminal):
    """헤더 매핑 기반 표 파서. GET/POST 모두 지원.

    일부 터미널(PNC 등)은 세션 쿠키 없이 바로 호출하면 403. prime_url 이 있으면
    먼저 들러 쿠키(JSESSIONID)를 확보한 뒤 본 요청을 보낸다(클라우드 IP 대비).
    """
    url = terminal["schedule_url"]
    headers = dict(HEADERS)
    headers["Referer"] = terminal.get("referer", url)
    sess = _make_session(terminal)
    prime = terminal.get("prime_url")
    if prime:
        try:
            sess.get(prime, headers={"Referer": prime}, timeout=TIMEOUT)
        except requests.RequestException:
            pass
    if terminal.get("method", "GET").upper() == "POST":
        r = sess.post(url, data=terminal.get("post_data", {}),
                      headers=headers, timeout=TIMEOUT)
    else:
        r = sess.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    found = _best_table(soup)
    if not found:
        raise ValueError("선석배정 표를 찾지 못함(헤더 변경/프레임 가능)")
    table, fields = found

    records = []
    seen = set()
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < len(fields):
            continue
        row = {}
        for i, field in enumerate(fields):
            if field and i < len(cells):
                row[field] = _clean(cells[i].get_text(" ", strip=True))

        voyage_raw = row.get("voyage", "")
        if not voyage_raw or voyage_raw in {"-", ""}:
            continue
        voyage_key = voyage_raw.split("/")[0].split("(")[0].strip()  # 선사항차/연도 제거

        vessel = row.get("vessel", "")
        dedup = (voyage_key, vessel)
        if dedup in seen:
            continue
        seen.add(dedup)

        dis, load = row.get("discharge", ""), row.get("load", "")
        work = f"{dis}/{load}" if (dis or load) else ""

        berth = row.get("berth", "")
        zone = row.get("zone", "")          # BPT 감만/신선대
        if zone:
            berth = f"{zone} {berth}".strip()

        records.append({
            "terminal": terminal["code"],
            "voyage": voyage_key,
            "vessel": vessel,
            "route": row.get("route", ""),
            "operator": row.get("operator", ""),
            "cct": _norm_dt(row.get("cct", "")),
            "etb": _norm_dt(row.get("etb", "")),
            "etd": _norm_dt(row.get("etd", "")),
            "berth": berth,
            "work": work,
            "callsign": row.get("callsign", ""),
            "rotation": "",
            "raw": row,
        })
    return records


# ---------------------------------------------------------------------------
# HKT(허치슨 부산/감만) 선석배정현황 페이지 파서
#   custom.hktl.com/jsp/T01/sunsuk.jsp (euc-kr) — 선명·선사항차·선석·반입마감(Closing
#   Time)·양적하까지 풍부. 기존 /api/fetchSummary(선명=코드, 반입마감 없음) 대체.
#   표 헤더: 터미널|선사항차|선석|Bitt.F/A|접안예정일시|작업예정일시|출항예정일시|
#            Closing Time|Port/STBD|총물량 IN/OUT|QC배정|선명|선사|Route
# ---------------------------------------------------------------------------
def parse_hkt_berth(terminal):
    url = terminal["schedule_url"]
    headers = dict(HEADERS)
    headers["Referer"] = terminal.get("referer", url)
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "html.parser")

    # 'Closing Time' 헤더를 가진 데이터 표 찾기(메뉴 표 배제)
    table = None
    for t in soup.find_all("table"):
        for tr in t.find_all("tr")[:3]:
            hdr = [_clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if any("Closing" in h for h in hdr) and 5 < len(hdr) < 30:
                table = (t, tr, hdr)
                break
        if table:
            break
    if not table:
        raise ValueError("HKT 선석배정현황 표를 찾지 못함")
    tbl, header_tr, hdr = table

    def idx(*keys):
        for i, h in enumerate(hdr):
            if any(k in h for k in keys):
                return i
        return -1

    i_voy, i_berth, i_etb, i_etd = idx("선사항차"), idx("선석"), idx("접안예정"), idx("출항예정")
    i_cct, i_work, i_vessel = idx("Closing"), idx("총물량"), idx("선명")
    i_opr, i_route = idx("선사") if hdr.count("선사") else idx("선사"), idx("Route")
    # '선사'는 '선사항차'와 겹치므로 정확히 '선사'인 컬럼을 따로 탐색
    i_opr = next((j for j, h in enumerate(hdr) if h == "선사"), -1)

    def cell(cells, i):
        return _clean(cells[i].get_text(" ", strip=True)) if 0 <= i < len(cells) else ""

    def dttrim(s):
        return _norm_dt(s)[:16]          # '2026-06-23 03:00:00' -> '2026-06-23 03:00'

    records, seen = [], set()
    start = tbl.find_all("tr").index(header_tr) + 1
    for tr in tbl.find_all("tr")[start:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < len(hdr):
            continue
        voyage = cell(cells, i_voy).split("/")[0].strip()
        vessel = cell(cells, i_vessel)
        if not voyage and not vessel:
            continue
        key = (voyage, vessel)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "terminal": terminal["code"],
            "voyage": voyage,
            "vessel": vessel or voyage,
            "route": cell(cells, i_route),
            "operator": cell(cells, i_opr),
            "cct": dttrim(cell(cells, i_cct)),
            "etb": dttrim(cell(cells, i_etb)),
            "etd": dttrim(cell(cells, i_etd)),
            "berth": cell(cells, i_berth),
            "work": cell(cells, i_work),
            "callsign": "",
            "rotation": "",
            "raw": {hdr[j]: cell(cells, j) for j in range(len(hdr))},
        })
    return records


def _fmt_ymd(d, t):
    """('20260618','70000') 또는 ('2026/06/19','04:00') -> 'YYYY-MM-DD HH:MM'."""
    d = (d or "").replace("/", "").strip()      # YYYYMMDD
    t = (t or "").strip()
    if len(d) != 8:
        return ""
    date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    if ":" in t:                                  # 'HH:MM'
        hm = t[:5]
    else:                                         # 'HHMMSS'
        t = t.zfill(6)
        hm = f"{t[0:2]}:{t[2:4]}"
    return f"{date} {hm}".strip()


def parse_hkt(terminal):
    url = terminal["schedule_url"]
    headers = dict(HEADERS)
    headers["Referer"] = terminal.get("referer", url)
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    records = []
    seen = set()
    for w in data.get("work", []):
        voyage = (w.get("voyvvd") or "").strip()
        if not voyage:
            continue
        if voyage in seen:
            continue
        seen.add(voyage)
        records.append({
            "terminal": terminal["code"],
            "voyage": voyage,
            "vessel": voyage,            # 선명 비공개 → 모선코드로 대체
            "route": "",
            "operator": "",
            "cct": "",                   # 허치슨 JSON엔 반입마감 없음
            "etb": _fmt_ymd(w.get("voyebd"), w.get("voyebt")),
            "etd": _fmt_ymd(w.get("voyedd"), w.get("voyedt")),
            "berth": (w.get("voybno") or "").strip(),
            "work": "",
            "callsign": "",
            "rotation": "",
            "raw": w,
        })
    return records


PARSERS = {
    "esvc_g": parse_esvc_g,
    "table": parse_table,
    "hkt_json": parse_hkt,        # (구) /api/fetchSummary — 데이터 부실, 미사용
    "hkt_berth": parse_hkt_berth,  # 선석배정현황 sunsuk.jsp — 풍부
}


def scrape_terminal(terminal):
    """단일 터미널 스크래핑. (records, error) 반환."""
    parser = PARSERS.get(terminal["parser"])
    if parser is None:
        return [], f"파서 미정의: {terminal['parser']}"
    try:
        recs = parser(terminal)
        return recs, None
    except NotImplementedError as e:
        return [], f"미구현: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


def scrape_all():
    """enabled 터미널 전체 스크래핑. {terminal_code: {...}} 반환."""
    result = {}
    for t in config.TERMINALS:
        if not t.get("enabled"):
            continue
        # 클라우드(Render IP)에선 Cloudflare가 막는 PNC 등은 제외(로컬은 정상 수집)
        if ON_CLOUD and t.get("cloud_blocked"):
            continue
        recs, err = scrape_terminal(t)
        result[t["code"]] = {
            "name": t["name"],
            "port": t["port"],
            "records": recs,
            "error": err,
        }
    return result


if __name__ == "__main__":
    import json
    data = scrape_all()
    for code, info in data.items():
        print(f"[{code}] {info['name']} -> {len(info['records'])}건, err={info['error']}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
