"""
v6：从 .evn 读取调度配置。默认周三/周日 12:00（Asia/Shanghai）。
"""

import os

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()

from main import run

s = BlockingScheduler(timezone="Asia/Shanghai")
days = os.getenv("RUN_DAYS", "wed,sun")
hour = int(os.getenv("RUN_HOUR", "12"))
s.add_job(run, "cron", day_of_week=days, hour=hour, minute=0)
print(f"⏰ {days} {hour}:00 (Asia/Shanghai) 自动运行")
s.start()
