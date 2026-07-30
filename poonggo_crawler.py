import asyncio
import requests
import json
import re
import os
import sys
from playwright.async_api import async_playwright

# 백업 주소(기본값)를 없애고 pure 환경변수만 가져옵니다.
GAS_WEBAPP_URL = os.environ.get("GAS_URL")

if not GAS_WEBAPP_URL:
    print("오류: 구글 웹 앱 URL(GAS_URL)이 세팅되지 않았습니다.")
    sys.exit(1)

def parse_broadcast_time(time_raw):
    """'⏱ 16시간 08분' 형태의 문자열을 소수점 첫째 자리 시간으로 변환 (예: 16.1)"""
    if not time_raw:
        return 0.0
    try:
        numbers = re.findall(r'\d+', time_raw)
        if len(numbers) >= 2:
            hours = float(numbers[0])
            minutes = float(numbers[1])
            total_hours = hours + (minutes / 60.0)
            return round(total_hours, 1)
        elif len(numbers) == 1:
            if "시간" in time_raw:
                return round(float(numbers[0]), 1)
            else:
                return round(float(numbers[0]) / 60.0, 1)
    except Exception:
        pass
    return 0.0

async def crawl_poonggo_data(playwright_page, url):
    """지정한 풍고 URL에서 풍력, 누적시청자, 방송시간을 추출하는 함수"""
    result = {"poong": 0.0, "accum_viewers": 0.0, "time": 0.0}
    try:
        await playwright_page.goto(url)
        await playwright_page.wait_for_timeout(5000)
        
        # 1. 별풍선 합계 (풍력) 추출
        # 구조: '별풍선 합계' 텍스트를 가진 span을 찾고, 그 부모 div 안의 h3를 가져옵니다.
        poong_xpath = "//span[text()='별풍선 합계']/ancestor::div[contains(@class, 'b')][1]//h3"
        try:
            await playwright_page.wait_for_selector(f"xpath={poong_xpath}", timeout=3000)
            poong_raw = await playwright_page.locator(f"xpath={poong_xpath}").first.text_content()
            if poong_raw:
                clean_poong = re.sub(r'[^0-9]', '', poong_raw)
                if clean_poong.strip() != "":
                    result["poong"] = round(float(clean_poong) / 10000.0, 4)
        except Exception:
            pass 

        # 2. 누적 시청자 추출
        # 구조: '누적 시청자' 텍스트를 가진 span을 찾고, 그 부모 div 안의 h3를 가져옵니다.
        viewers_xpath = "//span[text()='누적 시청자']/ancestor::div[contains(@class, 'b')][1]//h3"
        try:
            await playwright_page.wait_for_selector(f"xpath={viewers_xpath}", timeout=3000)
            viewers_raw = await playwright_page.locator(f"xpath={viewers_xpath}").first.text_content()
            if viewers_raw:
                clean_viewers = re.sub(r'[^0-9.]', '', viewers_raw)
                if clean_viewers.strip() != "":
                    result["accum_viewers"] = float(clean_viewers)
        except Exception:
            pass 

        # 3. 방송시간 추출
        # 구조: '방송시간' 텍스트를 가진 span을 찾고, 그 부모 div 안의 h3를 가져옵니다.
        time_xpath = "//span[text()='방송시간']/ancestor::div[contains(@class, 'b')][1]//h3"
        try:
            await playwright_page.wait_for_selector(f"xpath={time_xpath}", timeout=3000)
            time_raw = await playwright_page.locator(f"xpath={time_xpath}").first.text_content()
            result["time"] = parse_broadcast_time(time_raw)
        except Exception:
            pass 
            
        return result
    except Exception:
        return result

async def main():
    print("1. 구글 시트에서 풍고 수집 대상 목록을 불러오는 중...")
    try:
        response = requests.get(f"{GAS_WEBAPP_URL}?action=getPoongTargetList")
        if not response.text.strip().startswith(('[', '{')):
            print("\n[오류] 구글 시트 응답이 올바른 형식이 아닙니다.")
            print(response.text[:200])
            return
        streamer_list = response.json()
        print(f" -> 총 {len(streamer_list)}개의 대상을 확인했습니다.\n")
    except Exception as e:
        print(f"GAS 데이터 로드 실패: {e}")
        return

    if not streamer_list:
        print("수집할 대상이 없습니다.")
        return

    payload_to_update = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        print("2. 풍고 방송 데이터 크롤링 시작 (백그라운드)")
        for idx, streamer in enumerate(streamer_list):
            row_num = streamer.get('rowNum') # ◀ GAS가 준 행 번호 추출
            s_id = streamer['sId']
            s_name = streamer.get('name', '알 수 없음')
            url = streamer.get('poongUrl')
            
            if not url or url.index("http") != 0:
                continue

            # 출력창에 몇 번째 행(Row)을 작업 중인지도 명시해 줍니다.
            print(f" [{idx+1}/{len(streamer_list)}] 시트 {row_num}행 - 스트리머: {s_name} ({s_id}) 크롤링 중...")
            
            data_res = await crawl_poonggo_data(page, url)
            print(f"   -> 추출 성공 | 풍력(만): {data_res['poong']} | 누적시청자: {data_res['accum_viewers']} | 방송시간: {data_res['time']}")
            
            payload_to_update.append({
                "rowNum": row_num, # ◀ 업데이트할 때 다시 행 번호를 보냄
                "sId": s_id,
                "poong": data_res["poong"],
                "accumViewers": data_res["accum_viewers"],
                "broadcastTime": data_res["time"]
            })
            
            await page.wait_for_timeout(1500)
            
        await browser.close()

    if payload_to_update:
        print(f"\n3. 크롤링 완료된 {len(payload_to_update)}건의 데이터를 구글 시트에 전송 중...")
        post_data = {"action": "updatePoongGoData", "payload": payload_to_update}
        try:
            res = requests.post(GAS_WEBAPP_URL, data=json.dumps(post_data), headers={"Content-Type": "application/json"})
            print(f" -> 구글 시트 응답결과: {res.text}")
        except Exception as e:
            print(f"구글 시트 전송 중 오류 발생: {e}")

if __name__ == "__main__":
    asyncio.run(main())
