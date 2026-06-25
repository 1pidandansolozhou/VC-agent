import requests
from datetime import datetime

BASE = 'http://localhost:8001'
r = requests.post(f'{BASE}/api/v1/wx/auth/login', data={'username':'admin','password':'admin123'}, timeout=5)
token = r.json()['data']['access_token']
headers = {'Authorization': f'Bearer {token}'}

# Check articles
r = requests.get(f'{BASE}/api/v1/wx/articles?limit=20', headers=headers, timeout=15)
data = r.json()
arts = data.get('data', {}).get('list', [])

print(f'Articles returned: {len(arts)}')
if not arts:
    print('NO ARTICLES AT ALL')
    print('Response keys:', list(data.keys()))
    print('Response:', str(r.text)[:500])
else:
    # Sort by publish_time desc
    sorted_arts = sorted(arts, key=lambda x: x.get('publish_time', 0) or 0, reverse=True)
    for a in sorted_arts[:10]:
        pt = a.get('publish_time', 0) or 0
        ts = datetime.fromtimestamp(pt).strftime('%m-%d %H:%M') if pt else 'N/A'
        name = (a.get('mp_name') or '?')[:18]
        title = (a.get('title') or '?')[:50]
        print(f'  {ts} | {name} | {title}')

    # Check total count
    total = data.get('data', {}).get('total', '?')
    print(f'\nTotal articles (from API): {total}')
