"""Account primitives. Passwords never enter prompts or the application database."""
import secrets
import string
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime, parseaddr
from urllib.parse import urlsplit
from bs4 import BeautifulSoup


class Vault:
    def get(self, origin, email):
        import keyring
        return keyring.get_password("local-job-agent:" + origin, email)

    def set(self, origin, email, password):
        import keyring
        keyring.set_password("local-job-agent:" + origin, email, password)

    @staticmethod
    def generate():
        return "Aa1!" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))


def verification_candidates(message, recipient, sender_domains, link_domains, since):
    """Extract candidates only; email content never authorizes a browser action.

    Domains must be explicitly configured for the account being recovered.
    This is correlation, not sender authentication; review remains required.
    """
    try:
        date = parsedate_to_datetime(message.get("Date", ""))
        if date.tzinfo is None:
            return []
        if date < since or date > datetime.now(timezone.utc):
            return []
    except (ValueError, TypeError):
        return []
    sender = parseaddr(message.get("From", ""))[1].lower().split("@")[-1]
    if sender not in sender_domains or parseaddr(message.get("To", ""))[1].lower() != recipient.lower():
        return []
    result = []
    for part in message.walk():
        if part.get_content_type() not in ("text/plain", "text/html") or part.get_content_disposition() == "attachment":
            continue
        body = part.get_content()
        soup = BeautifulSoup(body, "html.parser")
        urls = [a["href"] for a in soup.find_all("a", href=True)] + re.findall(r"https://[^\s<>\"']+", body)
        for url in urls:
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.hostname in link_domains and not parsed.username:
                result.append({"kind": "link", "value": url})
        for code in re.findall(r"(?:verification|security|activation|one.time)\s+code\s*[:is]*\s*(\d{4,8})\b", soup.get_text(" "), re.I):
            result.append({"kind": "code", "value": code})
    return list({(r["kind"], r["value"]): r for r in result}.values())
