# -*- coding: utf-8 -*-
"""부두 공컨(EMPTY) 재고 스크래퍼.

로그인 없이 공개되는 터미널만 수집한다(공컨 자료는 모두 각 터미널 메인 페이지에 공개).
  - BPT(북항 신선대·감만): 선사별 × 규격(GP/DC·OT/UT·FL/PL/FC/FR·HQ/HC) × 20/40/45
  - HKT(북항 감만 허치슨): 기존 /api/fetchSummary 응답의 'empty' 배열(선사·size·type·수량)
  - BNCT(신항5): 메인 AJAX /esvc/getEmptyContainer (JSON: OPR/CNTR_TYP/CNTR_SIZ/PLAN_CNTR)
  - HPNT(신항4)·PNIT(신항2): mainPage.jsp 의 'Empty 반출 가능개수' 표(OPR×20DC/40DC/40(HC)/45DC)
  - HJNC(신항3): 메인 인라인 JSON(JSON.parse) PTNR_CODE/SZ/CNT/CON_TYPE
  - PNC(신항1): 메인 /info/ 인라인 표 contain-tbl (Size행 × 선사열)

미지원: DGT(emptyCntrAmount POST가 CSRF/세션 보호 → HTML 반환) → 추후.

정규화 레코드:
    {"terminal","zone","operator","group","size","qty"}   # qty>0 만
"""
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 25

BPT_URL = ("https://info.bptc.co.kr/content/od/frame/"
           "yard_new_empty_frame_od_kr.jsp?p_id=EMPT_NE_KR&snb_num=8&snb_div=service")
HKT_URL = "https://www.hktl.com/api/fetchSummary"
HKT_REFERER = "https://www.hktl.com/kor/Info/details.do?id=MD036"

# BPT 한 구역(zone)의 데이터 컬럼(선사 제외 9개)에 대응하는 (group, size)
BPT_COLS = [
    ("GP/DC", "20"), ("GP/DC", "40"),
    ("OT/UT", "20"), ("OT/UT", "40"),
    ("FL/PL/FC/FR", "20"), ("FL/PL/FC/FR", "40"),
    ("HQ/HC", "20"), ("HQ/HC", "40"), ("HQ/HC", "45"),
]


def _int(s):
    s = (s or "").strip().replace(",", "")
    return int(s) if s.isdigit() else 0


# 표준 규격 버킷(터미널별 제각각인 타입/사이즈를 공통 컬럼으로 묶음)
BUCKETS = ["20'", "40'DRY", "40'HQ", "45'", "냉동", "기타특수"]

# 냉동(리퍼) 타입 코드 / 그 외 특수(오픈탑·플랫·탱크 등)
_REEFER = ("RF", "RH", "NOR", "R0", "RE")
_OTHER_SPECIAL = ("OT", "UT", "FR", "FL", "PL", "FC", "TK")


def bucket(group, size):
    """(타입그룹, 사이즈) -> 표준 버킷명.

    예) ('GP/DC','20')->20'  ('HQ/HC','40')->40'HQ  ('DC','40H')->40'HQ
        ('RF','40')->냉동  ('OT/UT','40')->기타특수  ('DC','45')->45'
    """
    g = (group or "").upper().replace(" ", "")
    s = (size or "").upper()
    if any(k in g for k in _REEFER):
        return "냉동"
    if any(k in g for k in _OTHER_SPECIAL):
        return "기타특수"
    digits = "".join(c for c in s if c.isdigit())
    is_hc = ("HC" in g) or ("HQ" in g) or ("H" in s and not s.isdigit()) or "HC" in s or "HQ" in s
    if digits == "45":
        return "45'"
    if digits == "20":
        return "20'"
    if digits in ("40", "43"):
        return "40'HQ" if is_hc else "40'DRY"
    return "기타특수"


def scrape_bpt_empty():
    r = requests.get(BPT_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    table = None
    for t in soup.find_all("table"):
        if len(t.find_all("tr")) >= 5:
            table = t
            break
    if table is None:
        raise ValueError("BPT 공컨현황 표를 찾지 못함")

    rows = table.find_all("tr")
    out = []
    # r0=구역헤더, r1=선사/그룹, r2=20/40 서브헤더 → 데이터는 r3부터
    for tr in rows[3:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 20:
            continue
        # 신선대: cells[0]=선사, cells[1..9]=값 / 감만: cells[10]=선사, cells[11..19]=값
        for zone, op_idx, val_start in (("신선대", 0, 1), ("감만", 10, 11)):
            operator = cells[op_idx].strip()
            if not operator or operator in {"선사", "합계", "총계", "Total"}:
                continue
            for k, (group, size) in enumerate(BPT_COLS):
                qty = _int(cells[val_start + k])
                if qty > 0:
                    out.append({"terminal": "BPT", "zone": zone, "operator": operator,
                                "group": group, "size": size, "qty": qty})
    return out


def scrape_hkt_empty():
    r = requests.get(HKT_URL, headers={**HEADERS, "Referer": HKT_REFERER}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for e in data.get("empty", []):
        qty = _int(str(e.get("cnt")))
        if qty <= 0:
            continue
        out.append({
            "terminal": "HKT", "zone": "감만",
            "operator": (e.get("esqopr") or "").strip(),
            "group": (e.get("esqtyp") or "").strip(),   # DC, RF, OT ...
            "size": (e.get("esqsiz") or "").strip(),     # 20/40/45
            "qty": qty,
        })
    return out


BNCT_URL = "https://info.bnctkorea.com/esvc/getEmptyContainer"
BNCT_REFERER = "https://info.bnctkorea.com/esvc/"
HPNT_MAIN = "https://www.hpnt.co.kr/infoservice/main/mainPage.jsp"


def scrape_bnct_empty():
    import json as _json
    r = requests.get(BNCT_URL, headers={**HEADERS, "Referer": BNCT_REFERER,
                                        "X-Requested-With": "XMLHttpRequest"}, timeout=TIMEOUT)
    r.raise_for_status()
    arr = _json.loads(r.text)
    out = []
    for e in arr:
        qty = _int(str(e.get("PLAN_CNTR")))
        if qty <= 0:
            continue
        out.append({
            "terminal": "BNCT", "zone": "신항5부두",
            "operator": (e.get("OPR") or "").strip(),
            "group": (e.get("CNTR_TYP") or "").strip(),    # DC/FR/OT...
            "size": (e.get("CNTR_SIZ") or "").strip(),      # 20/40/43/45
            "qty": qty,
        })
    return out


def _split_size_type(col):
    """'20DC'->('20','DC'), '40(HC)'->('40','HC'), '45DC'->('45','DC')."""
    col = col.replace("(", "").replace(")", "").strip()
    size = "".join(ch for ch in col if ch.isdigit())
    typ = "".join(ch for ch in col if not ch.isdigit())
    return size, typ


def _scrape_infoservice_empty(url, terminal, zone):
    """infoservice 플랫폼(HPNT·PNIT) mainPage.jsp 의 'Empty 반출 가능개수' 표."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    table = None
    for cap in soup.find_all("caption"):
        if "Empty" in cap.get_text():
            table = cap.find_parent("table")
            break
    if table is None:
        raise ValueError(f"{terminal} Empty 표를 찾지 못함")

    rows = table.find_all("tr")
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    # header = ['OPR','20DC','40DC','40(HC)','45DC']
    cols = [_split_size_type(h) for h in header[1:]]
    out = []
    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < len(header):
            continue
        operator = cells[0].strip()
        if not operator or operator == "OPR":
            continue
        for k, (size, typ) in enumerate(cols):
            qty = _int(cells[k + 1])
            if qty > 0:
                out.append({"terminal": terminal, "zone": zone,
                            "operator": operator, "group": typ, "size": size, "qty": qty})
    return out


def scrape_hpnt_empty():
    return _scrape_infoservice_empty(HPNT_MAIN, "HPNT", "신항4부두")


def scrape_pnit_empty():
    return _scrape_infoservice_empty(
        "https://www.pnitl.com/infoservice/main/mainPage.jsp", "PNIT", "신항2부두")


# CON_TYPE 첫 글자 -> 규격 그룹(컨테이너 타입)
HJNC_TYPE = {"G": "DC", "R": "RF", "P": "FR", "U": "OT", "T": "TK"}


def scrape_hjnc_empty():
    """HJNC 메인 페이지에 인라인으로 박힌 JSON.parse('[...]') 배열 추출."""
    import json as _json
    import re as _re
    r = requests.get("https://www.hjnc.co.kr/esvc/", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    arr = None
    for c in _re.findall(r"JSON\.parse\('(\[.*?\])'\)", r.text, _re.S):
        if "PTNR_CODE" in c:
            arr = _json.loads(c.replace("\\'", "'"))
            break
    if arr is None:
        raise ValueError("HJNC 메인 인라인 empty JSON을 찾지 못함")
    out = []
    for e in arr:
        qty = _int(str(e.get("CNT")))
        if qty <= 0:
            continue
        sz = (e.get("SZ") or "").strip()                 # 20/40/40HC/45
        ctype = (e.get("CON_TYPE") or "G")[:1].upper()
        out.append({"terminal": "HJNC", "zone": "신항3부두",
                    "operator": (e.get("PTNR_CODE") or "").strip(),
                    "group": HJNC_TYPE.get(ctype, "DC"), "size": sz, "qty": qty})
    return out


def scrape_pnc_empty():
    """PNC 메인 /info/ 의 인라인 표 contain-tbl (Size행 × 선사열, 전치 구조)."""
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get("https://svc.pncport.com/info/", timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="contain-tbl")
    if table is None:
        raise ValueError("PNC Empty 표(contain-tbl)를 찾지 못함")
    rows = table.find_all("tr")
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    operators = header[1:]            # ['MAE','HLC','MSC',...]
    out = []
    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        size, typ = _split_size_type(cells[0])           # '20DC'->('20','DC')
        for k, op in enumerate(operators):
            qty = _int(cells[k + 1]) if k + 1 < len(cells) else 0
            if qty > 0:
                out.append({"terminal": "PNC", "zone": "신항1부두",
                            "operator": op.strip(), "group": typ, "size": size, "qty": qty})
    return out


EMPTY_SOURCES = {
    "PNC": scrape_pnc_empty,
    "PNIT": scrape_pnit_empty,
    "HJNC": scrape_hjnc_empty,
    "HPNT": scrape_hpnt_empty,
    "BNCT": scrape_bnct_empty,
    "BPT": scrape_bpt_empty,
    "HKT": scrape_hkt_empty,
}


def scrape_all_empty():
    """{terminal: {records, error}} 반환."""
    result = {}
    for code, fn in EMPTY_SOURCES.items():
        try:
            result[code] = {"records": fn(), "error": None}
        except Exception as e:  # noqa: BLE001
            result[code] = {"records": [], "error": f"{type(e).__name__}: {e}"}
    return result


def summarize(records):
    """선사별 합계(터미널 무관) + 전체 합계. 대시보드 요약용."""
    by_op = {}
    total = 0
    for r in records:
        by_op[r["operator"]] = by_op.get(r["operator"], 0) + r["qty"]
        total += r["qty"]
    ranked = sorted(by_op.items(), key=lambda x: -x[1])
    return {"total": total, "by_operator": ranked}


if __name__ == "__main__":
    data = scrape_all_empty()
    for code, info in data.items():
        recs = info["records"]
        s = summarize(recs)
        print(f"[{code}] {len(recs)}행, 총 {s['total']}개, err={info['error']}")
        print("   top선사:", s["by_operator"][:8])
