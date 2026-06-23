# -*- coding: utf-8 -*-
"""부산항 접안(선석배정) 스케줄 모니터 - 터미널 설정.

대부분의 부산 컨테이너 터미널은 동일한 eSvc 플랫폼(/esvc/vessel/berthScheduleG)을
사용하므로 하나의 파서(parser="esvc_g")로 처리한다. 구조가 다른 터미널은
parser 값을 다르게 주고 scraper.py 에 전용 어댑터를 추가한다.
"""

# 수집 대상 터미널 목록.
# enabled=False 인 항목은 어댑터 미구현(추후 추가) 상태.
TERMINALS = [
    # ---- 신항 (동일 eSvc 플랫폼) ----
    {
        "code": "HJNC",
        "name": "한진부산컨테이너터미널(신항 3부두)",
        "port": "신항",
        "base_url": "https://www.hjnc.co.kr",
        "parser": "esvc_g",
        "enabled": True,
    },
    {
        "code": "BNCT",
        "name": "비엔씨티(신항 5부두)",
        "port": "신항",
        "base_url": "http://info.bnctkorea.com",
        "parser": "esvc_g",
        "enabled": True,
    },
    {
        "code": "DGT",
        "name": "동원글로벌터미널(신항 8부두)",
        "port": "신항",
        "base_url": "https://info.dgtbusan.com/DGT",
        "parser": "esvc_g",
        "enabled": True,
    },
    # ---- 신항 (헤더 매핑 범용 표 파서) ----
    {
        "code": "PNC",
        "name": "부산신항만(신항 1부두)",
        "port": "신항",
        "parser": "table",
        "schedule_url": "https://svc.pncport.com/info/CMS/Ship/Info.pnc?mCode=MN014",
        # 세션 쿠키 없이 바로 호출하면 403 → 메인 먼저 들러 JSESSIONID 확보
        "prime_url": "https://svc.pncport.com/info/",
        "referer": "https://svc.pncport.com/info/",
        "enabled": True,
    },
    {
        "code": "PNIT",
        "name": "부산신항국제터미널(신항 2부두)",
        "port": "신항",
        "parser": "table",
        "schedule_url": "https://www.pnitl.com/infoservice/vessel/vslScheduleList.jsp",
        "enabled": True,
    },
    {
        "code": "HPNT",
        "name": "HMM PSA 신항만/현대부산신항만(신항 4부두)",
        "port": "신항",
        "parser": "table",
        "schedule_url": "https://www.hpnt.co.kr/infoservice/vessel/vslScheduleList.jsp",
        "enabled": True,
    },
    # 참고: 구 'PSA현대부산신항만'은 현재 HPNT(HMM PSA 신항만)와 동일 터미널 → HPNT로 통합.
    {
        "code": "BCT",
        "name": "부산컨테이너터미널(신항 2-4단계 서‘컨)",
        "port": "신항",
        "parser": "nexacro",
        "base_url": "https://info.bct2-4.com",
        # Nexacro14(RIA) 기반 → 데이터가 전용 SSV 프로토콜. 일반 스크래핑 불가, 미지원.
        "enabled": False,
    },

    # ---- 북항 ----
    {
        "code": "BPT",
        "name": "부산항터미널(북항 신선대·감만)",
        "port": "북항",
        "parser": "table",
        # 선석배정현황(T) 서블릿: POST로 기간/항로/정렬 파라미터 전송
        "schedule_url": "https://info.bptc.co.kr/Berth_status_text_servlet_sw_kr",
        "method": "POST",
        "post_data": {"v_time": "week", "ROCD": "ALL", "ORDER": "item1",
                      "v_gu": "A", "v_oper_cd": ""},
        "referer": "https://info.bptc.co.kr/content/sw/frame/berth_status_text_frame_sw_kr.jsp",
        "enabled": True,
    },
    {
        "code": "HKT",
        "name": "허치슨부산(북항 감만)",
        "port": "북항",
        "parser": "hkt_berth",
        # 선석배정현황(선명·선사항차·선석·반입마감·양적하 풍부). euc-kr.
        "schedule_url": "https://custom.hktl.com/jsp/T01/sunsuk.jsp",
        "referer": "https://custom.hktl.com/jsp/T01/sunsuk.jsp",
        "enabled": True,
    },
    {
        "code": "INTERGIS",
        "name": "인터지스(북항 7부두)",
        "port": "북항",
        "parser": "nexacro",
        "base_url": "https://www.e-iway.com",
        # e-iway 선석배정현황이 Nexacro17(RIA, SSV 전송 프로토콜) 기반 → 일반 스크래핑 불가.
        # 별도 작업: Nexacro 트랜잭션 서블릿/SSV 포맷 분석 필요.
        "enabled": False,
    },
]

# 조회 기간(오늘 기준 앞뒤 일수)
LOOKBACK_DAYS = 1
LOOKAHEAD_DAYS = 7

# 데이터 저장 위치
DATA_DIR = "data"

# 자동 수집 주기(초). 준실시간 폴링. env BERTH_INTERVAL 로 덮어쓸 수 있음.
import os as _os
COLLECT_INTERVAL = int(_os.environ.get("BERTH_INTERVAL", "300"))  # 기본 5분
