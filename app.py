# -*- coding: utf-8 -*-
"""부산항 접안 스케줄 대시보드 (로컬 프로토타입).

실행: python app.py  ->  http://localhost:5000
대시보드는 data/current.json, data/changes.json 을 읽어 보여준다.
'지금 수집' 버튼 또는 collect.py 스케줄러가 데이터를 갱신한다.
"""
import json
import os
import threading
import time

from flask import Flask, jsonify, render_template

import config
import monitor
import scraper
import scraper_empty

app = Flask(__name__)

# 컨테이너 조회(KL-NET, 회사계정 인증)는 공개 클라우드에 노출하지 않고 로컬에서만 제공.
# Render는 RENDER 환경변수를 자동 주입 → scraper.ON_CLOUD 로 판별.
CONTAINER_ENABLED = not scraper.ON_CLOUD
if CONTAINER_ENABLED:
    import klnet

# 마지막 수집 결과(상태 폴링용)
_last = {"collected_at": None, "changes": 0, "running": False}


def _collector_loop():
    """백그라운드 자동 수집 루프 (준실시간 폴링)."""
    while True:
        try:
            changes = monitor.run_once()
            _last["collected_at"] = monitor._now()
            _last["changes"] = len(changes)
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] 선석 자동수집 완료, 변동 {len(changes)}건")
        except Exception as e:  # noqa: BLE001
            print(f"[선석수집오류] {type(e).__name__}: {e}")
        try:
            emp = monitor.run_empty()
            n = sum(len(v.get("records", [])) for v in emp.values())
            print(f"[{time.strftime('%H:%M:%S')}] 공컨 자동수집 완료, {n}행")
        except Exception as e:  # noqa: BLE001
            print(f"[공컨수집오류] {type(e).__name__}: {e}")
        time.sleep(config.COLLECT_INTERVAL)


def start_collector():
    if _last["running"]:
        return
    _last["running"] = True
    t = threading.Thread(target=_collector_loop, daemon=True)
    t.start()


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


@app.route("/")
def index():
    current = _load(monitor.CURRENT, {"_meta": {}, "terminals": {}})
    changes = _load(monitor.CHANGES, [])
    return render_template(
        "dashboard.html",
        meta=current.get("_meta", {}),
        terminals=current.get("terminals", {}),
        changes=changes[:50],
        watch=monitor.WATCH_FIELDS,
        links=TERMINAL_LINKS,
        show_container=CONTAINER_ENABLED,
    )


# 부두 표시 순서(신항1→북항)
_TERMINAL_ORDER = ["PNC", "PNIT", "HJNC", "HPNT", "BNCT", "DGT", "BPT", "HKT"]

# 주요 선사 코드 -> 한글명(검색 편의용, 확실한 것만)
OPERATOR_NAMES = {
    "HMM": "HMM", "MSC": "MSC", "CMA": "CMA CGM", "ONE": "ONE",
    "HLC": "하팍로이드", "COS": "코스코", "COH": "코스코",
    "SKR": "장금상선", "KMD": "고려해운", "NSL": "남성해운",
    "SIT": "SITC", "WHL": "완하이", "YML": "양밍", "ZIM": "ZIM",
    "PIL": "PIL", "SML": "SM상선", "HAS": "흥아라인", "DJS": "동진상선",
    "MAE": "머스크", "EAS": "이스턴", "ESL": "이엠씨", "FEO": "FESCO",
    "DYS": "동영해운", "PCL": "팬오션", "POL": "포스에스엠",
}


@app.route("/empty")
def empty_page():
    """부두 공컨(EMPTY) 재고 현황 — 선사별 조회."""
    data = _load(monitor.EMPTY, {"_meta": {}, "terminals": {}})
    terminals = data.get("terminals", {})

    def torder(code):
        return _TERMINAL_ORDER.index(code) if code in _TERMINAL_ORDER else 99

    # (operator, terminal, zone) -> {bucket: qty}
    agg = {}
    for code, info in terminals.items():
        for r in info.get("records", []):
            b = scraper_empty.bucket(r.get("group"), r.get("size"))
            key = (r["operator"], code, r.get("zone") or code)
            agg.setdefault(key, {})
            agg[key][b] = agg[key].get(b, 0) + r["qty"]

    # 선사별로 묶기
    buckets = scraper_empty.BUCKETS
    ops = {}
    for (op, code, zone), bmap in agg.items():
        cells = [bmap.get(b, 0) for b in buckets]
        ops.setdefault(op, []).append({
            "terminal": code, "zone": zone, "cells": cells,
            "order": torder(code),
        })
    for op in ops:
        ops[op].sort(key=lambda x: x["order"])

    # 정렬된 선사 목록 + 이름
    op_list = sorted(ops.keys())
    operators = [{"code": op, "name": OPERATOR_NAMES.get(op, ""),
                  "rows": ops[op]} for op in op_list]

    return render_template(
        "empty.html",
        meta=data.get("_meta", {}),
        terminals=terminals,
        buckets=buckets,
        operators=operators,
        op_codes=op_list,
    )


# 터미널별 조회 페이지 바로가기(스크래핑 대신 직접 이동 — 차단 없이 전 터미널 커버).
TERMINAL_LINKS = [
    {"code": "PNC", "name": "부산신항만", "port": "신항1",
     "cntr": "https://svc.pncport.com/info/CMS/Container/Info.pnc?mCode=MN002",
     "copino": "https://svc.pncport.com/info/CMS/Edi/CopinoList.pnc?mCode=MN056"},
    {"code": "PNIT", "name": "부산신항국제터미널", "port": "신항2",
     "cntr": "https://www.pnitl.com/infoservice/cntr/cntrSearchList.jsp",
     "copino": "https://www.pnitl.com/infoservice/edi/ediGateCopinoReceiptList.jsp"},
    {"code": "HJNC", "name": "한진부산컨테이너터미널", "port": "신항3",
     "cntr": "https://www.hjnc.co.kr/esvc/cntr/info",
     "copino": "https://www.hjnc.co.kr/esvc/edocu/copino"},
    {"code": "HPNT", "name": "HMM PSA 신항만", "port": "신항4",
     "cntr": "https://www.hpnt.co.kr/infoservice/cntr/cntrSearchList.jsp",
     "copino": "https://www.hpnt.co.kr/infoservice/edi/ediGateCopinoReceiptList.jsp"},
    {"code": "BNCT", "name": "비엔씨티", "port": "신항5",
     "cntr": "https://info.bnctkorea.com/esvc/cntr/cntrSrch",
     "copino": "https://info.bnctkorea.com/esvc/edi/gateIOSrch"},
    {"code": "BCT", "name": "부산컨테이너터미널", "port": "신항",
     "cntr": "https://info.bct2-4.com/infoservice/index.html",
     "copino": "https://info.bct2-4.com/infoservice/index.html"},
    {"code": "DGT", "name": "동원글로벌터미널", "port": "신항8",
     "cntr": "https://info.dgtbusan.com/DGT/esvc/cntr/cntrInfo",
     "copino": "https://info.dgtbusan.com/DGT/esvc/edocu/gateIOSrch"},
    {"code": "BPT", "name": "부산항터미널(신선대·감만)", "port": "북항",
     "cntr": ("https://info.bptc.co.kr/content/cg/frame/cntr_frame_cg_kr.jsp"
              "?p_id=CONT_CN_KR&snb_num=1&snb_div=service"),
     "copino": ("https://info.bptc.co.kr/content/ed/frame/copino_query_frame_ed_kr.jsp"
                "?p_id=CPQR_ED_KR&snb_num=6&snb_div=service")},
    {"code": "HKT", "name": "허치슨부산(감만)", "port": "북항",
     "cntr": "https://custom.hktl.com/jsp/T04/dataio_cntr.jsp",
     "copino": "https://custom.hktl.com/jsp/T04/dataio_copino.jsp"},
]


@app.route("/links")
def links_page():
    """터미널별 컨테이너 조회 / 사전반출입(COPINO) 조회 바로가기."""
    return render_template("links.html", links=TERMINAL_LINKS)


if CONTAINER_ENABLED:
    @app.route("/container")
    def container_page():
        """KL-NET 컨테이너이동현황(국내) 통합 조회 화면 (로컬 전용)."""
        return render_template("container.html")

    @app.route("/api/container")
    def api_container():
        """컨테이너 번호로 전 터미널 이동이력 조회 (KL-NET eTrans, 로컬 전용)."""
        from flask import request
        cno = (request.args.get("no") or "").strip()
        if not cno:
            return jsonify({"ok": False, "message": "컨테이너 번호를 입력하세요.",
                            "count": 0, "rows": []})
        try:
            return jsonify(klnet.query_container(cno))
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "message": f"{type(e).__name__}: {e}",
                            "count": 0, "rows": []})


@app.route("/api/collect", methods=["POST"])
def api_collect():
    """수동 수집 트리거(선석+공컨)."""
    changes = monitor.run_once()
    try:
        monitor.run_empty()
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"ok": True, "changes": len(changes)})


@app.route("/api/collect_empty", methods=["POST"])
def api_collect_empty():
    """공컨 재고만 수동 재수집. 터미널별 수집 건수 반환."""
    emp = monitor.run_empty()
    counts = {code: {"rows": len(info.get("records", [])), "error": info.get("error")}
              for code, info in emp.items()}
    return jsonify({"ok": True, "collected_at": monitor._now(), "terminals": counts})


@app.route("/api/data")
def api_data():
    return jsonify(_load(monitor.CURRENT, {}))


@app.route("/api/status")
def api_status():
    """가벼운 상태 폴링용: 마지막 수집시각/변동수/주기."""
    cur = _load(monitor.CURRENT, {})
    return jsonify({
        "collected_at": cur.get("_meta", {}).get("collected_at"),
        "changes": _last["changes"],
        "interval": config.COLLECT_INTERVAL,
    })


# gunicorn(app:app)으로 띄워도 백그라운드 수집이 돌도록 import 시점에 시작.
# 루프 첫 회차가 즉시 run_once/run_empty 하므로 별도 초기수집 불필요.
start_collector()


if __name__ == "__main__":
    # 로컬 실행: PORT 환경변수 있으면 사용(없으면 5055)
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="0.0.0.0", port=port, debug=False)
