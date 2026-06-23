# -*- coding: utf-8 -*-
"""주기 수집 스케줄러.

사용:
    python collect.py            # 기본 10분 간격 반복 수집
    python collect.py --once     # 1회만 수집
    python collect.py --interval 300   # 5분 간격

대시보드(app.py)와 별도 프로세스로 띄워두면, 대시보드는 항상 최신
data/current.json 을 읽어 변동을 표시한다.
"""
import argparse
import time

import monitor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1회만 수집")
    ap.add_argument("--interval", type=int, default=600, help="수집 간격(초), 기본 600")
    args = ap.parse_args()

    while True:
        try:
            changes = monitor.run_once()
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] 수집 완료, 변동 {len(changes)}건")
            for c in changes[:10]:
                if c["type"] == "changed":
                    d = "; ".join(f"{f['label']} {f['old']}→{f['new']}" for f in c["fields"])
                    print(f"    [변경] {c['terminal']} {c['vessel']}: {d}")
                else:
                    lab = {"new": "신규", "removed": "취소"}[c["type"]]
                    print(f"    [{lab}] {c['terminal']} {c['vessel']}")
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] 수집 실패: {type(e).__name__}: {e}")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
