import sqlite3, sys

conn = sqlite3.connect('/app/data/db.db')
cur = conn.cursor()

# 查公众号订阅列表
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cur.fetchall()]
print('=== 所有表 ===')
for t in tables:
    print(t)

# 查 feeds/公众号
if 'feeds' in tables:
    cur.execute("PRAGMA table_info(feeds)")
    cols = [c[1] for c in cur.fetchall()]
    print(f'\n=== feeds 表字段: {cols} ===')
    cur.execute("SELECT * FROM feeds LIMIT 30")
    rows = cur.fetchall()
    print(f'共 {len(rows)} 个公众号订阅')
    for r in rows:
        print(r)

# 查文章统计
if 'articles' in tables:
    cur.execute("SELECT COUNT(*) FROM articles")
    total = cur.fetchone()[0]
    print(f'\n=== 文章总数: {total} ===')
    cur.execute("SELECT source, COUNT(*) as cnt FROM articles GROUP BY source ORDER BY cnt DESC LIMIT 20")
    for s, c in cur.fetchall():
        print(f'  {s}: {c}篇')
    # 时间范围
    cur.execute("SELECT MIN(created_at), MAX(created_at) FROM articles")
    mn, mx = cur.fetchone()
    print(f'\n时间范围: {mn} ~ {mx}')
elif 'article' in tables:
    cur.execute("SELECT COUNT(*) FROM article")
    total = cur.fetchone()[0]
    print(f'\n=== 文章总数(article表): {total} ===')
    cur.execute("PRAGMA table_info(article)")
    cols = [c[1] for c in cur.fetchall()]
    print(f'字段: {cols}')
    if 'source' in cols:
        cur.execute("SELECT source, COUNT(*) as cnt FROM article GROUP BY source ORDER BY cnt DESC LIMIT 20")
        for s, c in cur.fetchall():
            print(f'  {s}: {c}篇')
    if 'created_at' in cols:
        cur.execute("SELECT MIN(created_at), MAX(created_at) FROM article")
        mn, mx = cur.fetchone()
        print(f'\n时间范围: {mn} ~ {mx}')

conn.close()
