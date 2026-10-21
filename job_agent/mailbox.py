import imaplib
from email import policy
from email.parser import BytesParser
from .accounts import Vault, verification_candidates


class Gmail:
    """Read-only Gmail IMAP via app password kept in Keychain.

    OAuth can be added as an authentication provider. App passwords require an
    eligible Google account with 2-step verification; some managed accounts forbid them.
    """
    def __init__(self, email):
        self.email = email

    def candidates(self, sender_domains, link_domains, since):
        password = Vault().get("imap.gmail.com", self.email)
        if not password:
            raise ValueError("Gmail credential missing; run gmail-login")
        results = []
        with imaplib.IMAP4_SSL("imap.gmail.com") as client:
            client.login(self.email, password)
            client.select("INBOX", readonly=True)
            status, data = client.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
            if status != "OK":
                raise RuntimeError("Mailbox search failed")
            for uid in data[0].split()[-50:]:
                status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    continue
                for part in parts:
                    if isinstance(part, tuple):
                        message = BytesParser(policy=policy.default).parsebytes(part[1])
                        candidates = verification_candidates(message, self.email, sender_domains, link_domains, since)
                        results.extend({**c, "uid": uid.decode(), "needs_review": True} for c in candidates)
        return results
