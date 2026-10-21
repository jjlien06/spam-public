from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import pytest
from job_agent.accounts import verification_candidates, Vault
from job_agent.queue import parse_list


@pytest.mark.parametrize("sender,date,recipient", [
    ("attacker@evil.org", "fresh", "candidate@gmail.com"),
    ("accounts@example.com", "old", "candidate@gmail.com"),
    ("accounts@example.com", "future", "candidate@gmail.com"),
    ("accounts@example.com", "invalid", "candidate@gmail.com"),
    ("accounts@example.com", "fresh", "other@gmail.com"),
])
def test_mail_rejects_unrelated_or_stale(sender, date, recipient):
    now = datetime.now(timezone.utc)
    msg = EmailMessage()
    msg["From"], msg["To"] = sender, recipient
    times = {"fresh": now, "old": now-timedelta(days=2), "future": now+timedelta(days=2)}
    msg["Date"] = format_datetime(times[date]) if date in times else "invalid"
    msg.set_content("Verification code: 123456 https://example.com/activate")
    assert verification_candidates(msg, "candidate@gmail.com", ["example.com"], ["example.com"], now-timedelta(minutes=10)) == []


def test_password_generation():
    passwords = {Vault.generate() for _ in range(100)}
    assert len(passwords) == 100
    assert all(len(p) >= 24 for p in passwords)


def test_import_skips_closed_and_invalid_links():
    def row(url, text=""):
        return f'<tr><td>C</td><td>R</td><td>L</td><td>{text}<a href="{url}"><img alt="Apply"></a></td></tr>'
    html = '<table>' + row('javascript:alert(1)') + row('https://example.com/closed', '🔒') + row('https://example.com/open') + '</table>'
    assert [j['url'] for j in parse_list(html)] == ['https://example.com/open']
