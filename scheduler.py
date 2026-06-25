"""
v2：每日定时调度 — 默认每天 RUN_HOUR:00 (Asia/Shanghai) 自动运行。
"""

import os

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()

from main import run

s = BlockingScheduler(timezone="Asia/Shanghai")
hour = int(os.getenv("RUN_HOUR", "12"))
# v2: 每天执行，不再限制 wed,sun
s.add_job(run, "cron", hour=hour, minute=0)
print(f"⏰ 每天 {hour}:00 (Asia/Shanghai) 自动运行")
s.start()
