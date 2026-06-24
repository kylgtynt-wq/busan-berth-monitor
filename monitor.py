# -*- coding: utf-8 -*-
"""스냅샷 저장 + 변동 감지.

흐름:
  1) scraper.scrape_all() 로 현재 스케줄 수집
  2) 직전 스냅샷(latest.json)과 비교 → 변동 산출
  3) 새 스냅샷 저장 + 변동 이력(changes.json) 누적
  4) 대시보드가 읽을 current.json(변동 플래그 포함) 저장
"""
import datetime as dt
import json
import os

import config
import scraper
import scraper_empty

DATA_DIR = config.DATA_DIR
LATEST = os.path.join(DATA_DIR, "latest.json")        # 직전 원본 스냅샷
CURRENT = os.path.join(DATA_DIR, "current.json")      # 대시보드용(변동 플래그 포함)
CHANGES = os.path.join(DATA_DIR, "changes.json")      # 변동 이력 누적
EMPTY = os.path.join(DATA_DIR, "empty.json")          # 공컨(EMPTY) 재고 현황

# 변동 감지 대상 필드 (라벨)
WATCH_FIELDS = {
    "cct": "반입마감",
    "etb": "접안시간",
    "etd": "출항시간",
    "berth": "선석",
    "vessel": "선박명",
    "work": "양적하",
}


def _sort_key(rec):
    """반입마감일시 오름차순 정렬키(없으면 맨 뒤). 동률이면 ETB."""
    cct = rec.get("cct") or ""
    etb = rec.get("etb") or ""
    return (cct == "", cct or etb, etb)


KST = dt.timezone(dt.timedelta(hours=9))


def _now():
    # 한국시간(KST) 고정 — 클라우드(UTC) 서버에서도 한국시간으로 표시.
    return dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _save(path, obj):
    with open(path, encoding="utf-8", mode="w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _key(terminal_code, rec):
    return f"{terminal_code}::{rec['voyage']}"


def _index(snapshot):
    """{terminal: {records:[...]}} -> {key: rec}"""
    idx = {}
    for code, info in snapshot.items():
        for rec in info.get("records", []):
            idx[_key(code, rec)] = rec
    return idx


def diff(prev_snapshot, curr_snapshot):
    """직전/현재 스냅샷 비교 → 변동 리스트.

    각 변동: {type, terminal, voyage, vessel, fields:[{field,label,old,new}], at}
    type: new(신규) | changed(변경) | removed(취소/사라짐)
    """
    prev = _index(prev_snapshot) if prev_snapshot else {}
    curr = _index(curr_snapshot)
    now = _now()
    changes = []

    for key, rec in curr.items():
        if key not in prev:
            # 직전 스냅샷이 아예 없으면(최초수집) 신규로 도배하지 않음
            if prev_snapshot:
                changes.append({
                    "type": "new", "at": now,
                    "terminal": rec["terminal"], "voyage": rec["voyage"],
                    "vessel": rec["vessel"], "fields": [],
                })
            continue
        old = prev[key]
        changed_fields = []
        for field, label in WATCH_FIELDS.items():
            if (old.get(field) or "") != (rec.get(field) or ""):
                changed_fields.append({
                    "field": field, "label": label,
                    "old": old.get(field, ""), "new": rec.get(field, ""),
                })
        if changed_fields:
            changes.append({
                "type": "changed", "at": now,
                "terminal": rec["terminal"], "voyage": rec["voyage"],
                "vessel": rec["vessel"], "fields": changed_fields,
            })

    if prev_snapshot:
        for key, rec in prev.items():
            if key not in curr:
                changes.append({
                    "type": "removed", "at": now,
                    "terminal": rec["terminal"], "voyage": rec["voyage"],
                    "vessel": rec["vessel"], "fields": [],
                })
    return changes


def _annotate(curr_snapshot, changes):
    """현재 스냅샷 레코드에 변동 플래그를 붙여 대시보드용으로 가공."""
    # key -> change
    by_key = {}
    for c in changes:
        by_key[f"{c['terminal']}::{c['voyage']}"] = c
    for code, info in curr_snapshot.items():
        for rec in info.get("records", []):
            c = by_key.get(_key(code, rec))
            if c and c["type"] == "new":
                rec["_change"] = "new"
                rec["_changed_fields"] = []
            elif c and c["type"] == "changed":
                rec["_change"] = "changed"
                rec["_changed_fields"] = [f["field"] for f in c["fields"]]
                rec["_change_detail"] = c["fields"]
            else:
                rec["_change"] = ""
                rec["_changed_fields"] = []
    return curr_snapshot


def run_once():
    """1회 수집 + 변동 감지 + 저장. 변동 리스트 반환."""
    _ensure_dir()
    prev = _load(LATEST, None)
    curr = scraper.scrape_all()

    # 에러로 0건이 된 터미널은 직전 데이터를 유지(빈수집으로 '전부 취소' 오탐 방지)
    if prev:
        for code, info in curr.items():
            if info.get("error") and not info.get("records"):
                if code in prev and prev[code].get("records"):
                    info["records"] = prev[code]["records"]
                    info["stale"] = True

    changes = diff(prev, curr)

    # 반입마감일시 기준 오름차순 정렬
    for info in curr.values():
        if info.get("records"):
            info["records"].sort(key=_sort_key)

    # 이력 누적 (최근 500건 유지)
    history = _load(CHANGES, [])
    history = changes + history
    history = history[:500]

    # 저장
    meta = {"collected_at": _now()}
    curr_with_meta = {"_meta": meta, "terminals": _annotate(curr, changes)}
    _save(CURRENT, curr_with_meta)
    _save(LATEST, curr)  # 다음 비교용 원본(플래그 없는 깨끗한 스냅샷)
    _save(CHANGES, history)

    return changes


def run_empty():
    """공컨(EMPTY) 재고 1회 수집 + 저장. {terminal:{records,error}} 반환.

    수집 실패한 터미널은 직전 데이터를 유지(stale)해 빈 화면 오탐 방지.
    """
    _ensure_dir()
    prev = _load(EMPTY, None)
    curr = scraper_empty.scrape_all_empty()

    if prev and prev.get("terminals"):
        for code, info in curr.items():
            if info.get("error") and not info.get("records"):
                pinfo = prev["terminals"].get(code)
                if pinfo and pinfo.get("records"):
                    info["records"] = pinfo["records"]
                    info["stale"] = True

    out = {"_meta": {"collected_at": _now()}, "terminals": curr}
    _save(EMPTY, out)
    return curr


if __name__ == "__main__":
    chs = run_once()
    print(f"수집 완료 @ {_now()}  변동 {len(chs)}건")
    for c in chs[:20]:
        if c["type"] == "changed":
            d = "; ".join(f"{f['label']} {f['old']}→{f['new']}" for f in c["fields"])
            print(f"  [변경] {c['terminal']} {c['vessel']}({c['voyage']}): {d}")
        else:
            label = {"new": "신규", "removed": "취소"}[c["type"]]
            print(f"  [{label}] {c['terminal']} {c['vessel']}({c['voyage']})")
