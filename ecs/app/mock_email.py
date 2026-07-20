from __future__ import annotations

import sys
from datetime import datetime, timezone

class MockEmailLogger:
    @staticmethod
    def _send(to_email: str, subject: str, body: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        message = (
            f"\n{'='*60}\n"
            f"MOCK EMAIL DISPATCH\n"
            f"Time:    {now}\n"
            f"To:      {to_email}\n"
            f"Subject: {subject}\n"
            f"{'-'*60}\n"
            f"{body}\n"
            f"{'='*60}\n"
        )
        sys.stdout.write(message)
        sys.stdout.flush()

    @staticmethod
    def send_contradiction_alert(to_email: str, team_name: str, contradiction_details: str) -> None:
        subject = f"[Action Required] Wiki Contradiction Detected in Team '{team_name}'"
        body = (
            f"Hello,\n\n"
            f"Our automated background worker has detected a potential contradiction in the wiki entries for your team '{team_name}'.\n\n"
            f"Details:\n"
            f"{contradiction_details}\n\n"
            f"Please review the wiki and resolve this conflict as soon as possible.\n\n"
            f"Best regards,\n"
            f"The System"
        )
        MockEmailLogger._send(to_email, subject, body)

    @staticmethod
    def send_unusual_activity(captain_email: str, team_name: str, member_username: str, activity_desc: str) -> None:
        subject = f"[Security Alert] Unusual Activity Detected for Team '{team_name}'"
        body = (
            f"Hello Team Captain,\n\n"
            f"We have detected unusual activity from team member '{member_username}' in team '{team_name}'.\n\n"
            f"Activity Details:\n"
            f"{activity_desc}\n\n"
            f"Please review this activity. You can manage your team members and revoke access from the Operations Dashboard if necessary.\n\n"
            f"Best regards,\n"
            f"The System"
        )
        MockEmailLogger._send(captain_email, subject, body)
