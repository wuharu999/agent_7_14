"""Operator checks and explicit test sending: python -m ecs.app.email_admin --help."""
from __future__ import annotations

import argparse
import html
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ecs.app import database, email_store, email_notifications as mail


def main() -> None:
    parser = argparse.ArgumentParser(description='模板邮件配置检查、预览及管理员测试')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check', help='只验证本地配置；不连接邮箱，不输出密码')
    preview = commands.add_parser('preview', help='生成使用示例数字的周报 HTML；不读取业务数据，不发邮件')
    preview.add_argument('--output', type=Path, required=True)
    commands.add_parser('status', help='显示持久发件队列状态计数；不发邮件')
    test = commands.add_parser('send-test', help='立即向一个已启用管理员的账户邮箱发送测试邮件')
    test.add_argument('--to', required=True)
    args = parser.parse_args()
    settings = mail.Settings.from_env()
    if args.command == 'preview':
        start, end = mail.weekly_window(datetime.now(timezone.utc))
        stats = dict(questions=128, conversations=43, client_ips=25, active_users=3,
            uploads=12, upload_bytes=5 * 1024**2, contradictions=1, warnings=2,
            actions=[dict(action='login', count=9), dict(action='upload_source', count=12)],
            users=[dict(username='示例管理员', actions=8, uploads=5)])
        subject, body = mail.weekly_message(start, end, stats)
        args.output.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>邮件模板预览</title><body style="font-family:Arial,Microsoft YaHei,sans-serif;line-height:1.7">'
            '<p>仅供预览：以下数字均为示例。</p>' + f'<h2>{html.escape(subject)}</h2>'
            f'<div style="white-space:pre-wrap">{html.escape(body)}</div></body></html>', encoding='utf-8')
        print('示例邮件已写入指定文件；未发送。')
        return
    if args.command == 'check':
        settings.validate()
        print('邮件配置有效；未连接 SMTP。' if settings.enabled else '邮件已停用。配置完成后设置 EMAIL_ENABLED=true。')
        return
    # Read existing production schema, never initialize users or other business tables here.
    email_store.initialize()
    if args.command == 'status':
        with database._connect() as db:
            counts = dict(db.execute('SELECT status, COUNT(*) FROM email_outbox GROUP BY status').fetchall())
        print(json.dumps(counts, ensure_ascii=False))
        return
    settings.validate()
    if not settings.enabled:
        parser.error('请先完成配置并设置 EMAIL_ENABLED=true。')
    user = next((u for u in email_store.admins() if u['email'] == args.to and mail.valid_address(args.to)), None)
    if user is None:
        parser.error('收件人必须是已启用管理员的账户邮箱。')
    # A direct test is intentional and separate from the production queue/digest.
    mail.deliver({'event_key': 'test:' + uuid.uuid4().hex, 'recipient': args.to,
        'subject': '【测试邮件】机器人知识平台',
        'body': '这是一封人工触发的固定模板测试邮件。若您能正常阅读中文，发信配置已通过基本检查。',
        'created_at': datetime.now(timezone.utc).timestamp()}, settings)
    print('SMTP 已接受测试邮件；请检查收件箱及垃圾邮件。')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not expose SMTP responses, which may contain credentials or addresses.
        raise SystemExit('操作失败（' + type(exc).__name__ + '）；请检查配置及发件队列状态。') from None
