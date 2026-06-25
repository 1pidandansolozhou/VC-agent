"""刷新全部公众号 + 统计窗口内文章数"""
import requests, time, sys
from datetime import datetime

BASE = 'http://localhost:8001'

r = requests.post(f'{BASE}/api/v1/wx/auth/login', data={'username':'admin','password':'admin123'}, timeout=5)
token = r.json()['data']['access_token']
headers = {'Authorization': f'Bearer {token}'}

# 获取所有活跃公众号
r = requests.get(f'{BASE}/api/v1/wx/mps?limit=100', headers=headers, timeout=10)
feeds = r.json().get('data', {}).get('list', [])
active = [f for f in feeds if f.get('status', 1) == 1]
print(f'逐个刷新 {len(active)} 个公众号...')

refreshed = 0
for f in active:
    fid = f.get('id', '')
    try:
        r = requests.get(f'{BASE}/api/v1/wx/mps/{fid}/refresh', headers=headers, timeout=10)
        if r.status_code == 200:
            refreshed += 1
    except Exception:
        pass
    if refreshed > 0 and refreshed % 10 == 0:
        print(f'  已刷新 {refreshed}/{len(active)}...')

print(f'刷新完成: {refreshed}/{len(active)}')
print('等待30秒拉取数据...')
sys.stdout.flush()
time.sleep(30)

ts_s = int(datetime(2026, 6, 24).timestamp())
total = 0
active_mps = set()
offset = 0
old_pages = 0
while offset < 1000:
    r = requests.get(f'{BASE}/api/v1/wx/articles?limit=100&offset={offset}', headers=headers, timeout=15)
    arts = r.json().get('data', {}).get('list', [])
    if not arts:
        break
    page_in = 0
    for a in arts:
        pt = a.get('publish_time', 0) or 0
        if pt >= ts_s:
            total += 1
            active_mps.add(a.get('mp_id', ''))
            page_in += 1
    if page_in == 0:
        old_pages += 1
        if old_pages >= 2:
            break
    else:
        old_pages = 0
    offset += 100

print(f'\n✅ 窗口内文章: {total} 篇 ({len(active_mps)} 个公众号)')
