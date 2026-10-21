"""Private credentials configuration and durable, one-attempt operation tracking."""
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit


def origin_of(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Credentials require an HTTPS origin without embedded credentials")
    return "https://" + parsed.netloc.lower()


class AccountStore:
    def __init__(self, path, state):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())
        self.path.chmod(0o600)
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / "account_attempts.sqlite3")
        (state / "account_attempts.sqlite3").chmod(0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts (origin TEXT,email TEXT,operation TEXT,status TEXT,PRIMARY KEY(origin,email,operation))")
        self.db.execute("CREATE TABLE IF NOT EXISTS terms (origin TEXT, accepted_at TEXT DEFAULT CURRENT_TIMESTAMP, text TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS rejected (origin TEXT, email TEXT, fingerprint TEXT, PRIMARY KEY(origin, email, fingerprint))")

    def credentials(self, url):
        origin = origin_of(url)
        override = self.data.get("accounts", {}).get(origin)
        result = dict(self.data.get("default", {}))
        if override:
            result.update(override)
        if not result.get("email") or not result.get("password"):
            raise ValueError("Account email/password missing")
        return origin, result, bool(override and override.get("exists", False))

    def claim(self, origin, email, operation):
        """Reserve one attempt. Creation is single-shot until reconciled; sign-in is
        idempotent, so any earlier outcome (timed out, rejected) may be retried."""
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO attempts VALUES (?,?,?,?)", (origin, email, operation, "uncertain"))
            claimed = cursor.rowcount == 1
            if not claimed and operation == "login":
                cursor = self.db.execute("UPDATE attempts SET status='uncertain' WHERE origin=? AND email=? AND operation='login'", (origin, email))
                claimed = cursor.rowcount == 1
        return claimed

    def finish(self, origin, email, operation, status):
        with self.db:
            self.db.execute("UPDATE attempts SET status=? WHERE origin=? AND email=? AND operation=?", (status, origin, email, operation))

    def was_rejected(self, origin, email, fingerprint):
        legacy = self.status(origin, email, "login") == "rejected:" + fingerprint
        return legacy or self.db.execute("SELECT 1 FROM rejected WHERE origin=? AND email=? AND fingerprint=?",
                                         (origin, email, fingerprint)).fetchone() is not None

    def reject(self, origin, email, fingerprint):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO rejected VALUES (?,?,?)", (origin, email, fingerprint))

    def fallback_passwords(self):
        return [p for p in self.data.get("default", {}).get("fallback_passwords", []) if isinstance(p, str) and p]

    def use_password(self, origin, password, exists=True):
        """Try another password for this origin in this run (not saved yet)."""
        self.data.setdefault("accounts", {}).setdefault(origin, {})
        self.data["accounts"][origin].update({"password": password, "exists": exists})

    def remember(self, origin, email, password):
        """Persist the password that worked for this origin."""
        on_disk = json.loads(self.path.read_text())
        entry = on_disk.setdefault("accounts", {}).setdefault(origin, {})
        entry.update({"email": email, "password": password, "exists": True})
        self.path.write_text(json.dumps(on_disk, indent=2))
        self.path.chmod(0o600)

    def status(self, origin, email, operation):
        row = self.db.execute("SELECT status FROM attempts WHERE origin=? AND email=? AND operation=?", (origin, email, operation)).fetchone()
        return row[0] if row else None

    def release(self, origin, email, operation):
        """Forget an attempt that provably never reached the site."""
        with self.db:
            self.db.execute("DELETE FROM attempts WHERE origin=? AND email=? AND operation=?", (origin, email, operation))

    def record_terms(self, origin, text):
        """Keep the exact consent text accepted for an account on this origin."""
        with self.db:
            self.db.execute("INSERT INTO terms(origin, text) VALUES (?,?)", (origin, text))

    def has_attempt(self, origin, email, operation):
        return self.db.execute("SELECT 1 FROM attempts WHERE origin=? AND email=? AND operation=?", (origin, email, operation)).fetchone() is not None
