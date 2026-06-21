import os
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import List


def send_email(files: List[str]):
    """预留：发送带附件的周报邮件。默认关闭。"""
    if os.getenv("ENABLE_EMAIL", "false").lower() != "true":
        return
    raise NotImplementedError("邮件推送预留，本期不接通")
