"""Choose which queued jobs are worth applying to, from Simplify listing data only.

Rules are deterministic and conservative: a job is skipped only on a positive
signal (an advanced-degree marker, every location outside the US, delisting).
"""
import re

US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
                "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR".split())
FOREIGN = re.compile(
    r"\b(Canada|UK|United Kingdom|England|Scotland|Wales|Ireland|India|Germany|France|Netherlands|Poland|Spain|"
    r"Italy|Switzerland|Sweden|Denmark|Israel|Singapore|Japan|China|Hong Kong|Taiwan|Korea|Australia|(?<!New )Mexico|"
    r"Brazil|United Arab Emirates|UAE|Philippines|Vietnam|Czechia|Czech Republic|Romania|Portugal|Belgium|"
    r"Austria|Finland|Norway|Hungary|Greece|Turkey|Egypt|Nigeria|Kenya|South Africa|Argentina|Chile|Colombia|"
    r"Costa Rica|Luxembourg|Estonia|Serbia|Ukraine|Malaysia|Thailand|Indonesia|New Zealand|Saudi Arabia|Qatar)\b"
    r"|,\s*(ON|BC|AB|QC|MB|SK|NS|NB|NL|PE)$")
PREFIX = "targeting: "
MANUAL_NOTE = PREFIX + "apply yourself (priority company)"


def foreign(place):
    state = re.search(r",\s*([A-Z]{2})$", place)
    return bool(FOREIGN.search(place)) and not (state and state.group(1) in US_STATES)


def reason_skipped(job, prefs):
    """Why this job should not be applied to, or None when it is a target."""
    if not job.get("listed", 1):
        return "no longer listed"
    excluded = {name.strip().lower() for name in prefs.get("exclude_companies", [])}
    if (job.get("company") or "").strip().lower() in excluded:
        return "company excluded"
    role = job.get("role") or ""
    if "2026" in role and "2027" not in role:
        return "past season (2026)"
    if prefs.get("categories") and job.get("category") and job["category"] not in prefs["categories"]:
        return "category not selected"
    if prefs.get("skip_advanced_degree") and "🎓" in (job.get("flags") or ""):
        return "advanced degree required"
    places = [p for p in (job.get("locations") or "").split("; ") if p]
    if prefs.get("us_only") and places and all(foreign(p) for p in places):
        return "no US location"
    # Degree level always rules a posting out; a field word ("marketing", "tax")
    # only when the title has no CS/data word ("Tax Technology AI Intern" stays).
    if any(re.search(pattern, role, re.IGNORECASE) for pattern in prefs.get("skip_title_patterns", [])):
        return "degree level not yours"
    if any(re.search(pattern, role, re.IGNORECASE) for pattern in prefs.get("skip_field_patterns", [])) and \
            not any(re.search(pattern, role, re.IGNORECASE) for pattern in prefs.get("keep_title_patterns", [])):
        return "role outside your field"
    # Optional whitelist: only titles that name the user's kind of work (e.g. CS),
    # and never ones that name a kind they ruled out ("Software Product Manager").
    if any(re.search(pattern, role, re.IGNORECASE) for pattern in prefs.get("reject_title_patterns", [])):
        return "not a role type you want"
    required = prefs.get("require_title_patterns")
    if required and not any(re.search(pattern, role, re.IGNORECASE) for pattern in required):
        return "not a role type you want"
    return None


ROLE_NOISE = re.compile(r"\([^)]*\)|\b(?:summer|fall|spring|winter|20\d\d|interns?|internships?|co-?op|undergraduate|university|usa?)\b", re.I)
# Statuses that mean this role at this company is already applied to or being applied to.
TAKEN = ("submitted", "applied", "already_applied", "submit_uncertain", "hand_submit", "spam_flagged", "review")


def role_key(job):
    """Company + title without season, year and "intern" words: other listings of the same
    role (QTX "Software Engineer Intern" at four sites, DraftCastle' two Boston
    "Analytics Intern" requisitions) share it; a different team or title does not."""
    company = re.sub(r"[^a-z0-9]+", " ", (job.get("company") or "").lower()).strip()
    title = re.sub(r"[^a-z0-9]+", " ", ROLE_NOISE.sub(" ", job.get("role") or "").lower()).strip()
    return company, title


def is_manual(job, prefs):
    """A target at a company the user applies to by hand (big tech, top quant)."""
    names = [name.strip().lower() for name in prefs.get("manual_companies", [])]
    company = re.sub(r"[^a-z0-9 ]", "", (job.get("company") or "").lower())
    return any(re.sub(r"[^a-z0-9 ]", "", name) == company for name in names)


def apply_targeting(queue, prefs):
    """Skip untouched queued jobs that miss the targets; restore earlier
    targeting skips that match again. Targets at manual_companies become
    "manual" (the user applies to those by hand). Jobs the Simplify tracker lists
    as applied become "applied" for good. Other statuses are never changed."""
    applied = {row["job_key"]: row["applied_date"] for row in
               queue.db.execute("SELECT job_key, applied_date FROM tracked WHERE status='APPLIED'")}
    rows = [dict(r) for r in queue.db.execute("SELECT *, rowid AS position FROM jobs ORDER BY rowid")]
    ours = lambda job: job["status"] in ("skipped", "manual") and job["note"].startswith(PREFIX)
    open_rows = lambda job: job["status"] == "queued" or ours(job)
    # A job already worked on under any listing blocks its other listings.
    worked = {job["job_key"] for job in rows if not open_rows(job)} | set(applied)
    # Optional ("one_application_per_role"): another listing of a role already applied
    # to (a second site, a re-posted requisition) is not applied to again.
    taken_roles = {role_key(job) for job in rows if job["status"] in TAKEN}
    skipped, kept = {}, set()
    with queue.db:
        for job in rows:
            if not open_rows(job):
                continue
            if job["job_key"] in applied:
                queue.db.execute("UPDATE jobs SET status='applied', note=? WHERE id=?",
                                 ("Simplify tracker: applied " + applied[job["job_key"]], job["id"]))
                skipped["already applied"] = skipped.get("already applied", 0) + 1
                continue
            reason = reason_skipped(job, prefs)
            if not reason and (job["job_key"] in worked or job["job_key"] in kept):
                reason = "duplicate listing"
            if not reason and prefs.get("one_application_per_role") and role_key(job) in taken_roles:
                reason = "same role already applied at this company"
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                queue.db.execute("UPDATE jobs SET status='skipped', note=? WHERE id=?", (PREFIX + reason, job["id"]))
            else:
                kept.add(job["job_key"])
                if is_manual(job, prefs):
                    skipped["apply yourself"] = skipped.get("apply yourself", 0) + 1
                    queue.db.execute("UPDATE jobs SET status='manual', note=? WHERE id=?", (MANUAL_NOTE, job["id"]))
                elif ours(job):
                    queue.db.execute("UPDATE jobs SET status='queued', note='' WHERE id=?", (job["id"],))
    eligible = ranked(queue)
    by = lambda key: dict(sorted(((v, sum(1 for j in eligible if j[key] == v)) for v in {j[key] for j in eligible}),
                                 key=lambda item: -item[1]))
    return {"eligible": len(eligible), "saved": sum(1 for j in eligible if j["saved"]), "skipped": skipped,
            "by_platform": by("platform"), "by_category": by("category")}


def ranked(queue):
    """Queued jobs in apply order: newest on the Simplify list first (fresh postings
    are the ones still open), then saved in Simplify, FAANG+, list order. Jobs with
    no listing age (tracker-only saves) come last."""
    rows = queue.db.execute("SELECT jobs.*, jobs.rowid AS position, tracked.status = 'SAVED' AS saved FROM jobs"
                            " LEFT JOIN tracked ON tracked.job_key = jobs.job_key WHERE jobs.status='queued'").fetchall()
    return sorted((dict(r) for r in rows), key=lambda j: (j["age_days"] if j["age_days"] is not None else 10**6,
                                                          not j["saved"], "🔥" not in (j["flags"] or ""), j["position"]))
