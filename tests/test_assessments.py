import json

from job_agent import assessments
from tests.test_ops import job, setup


def mail(i, date, subject, snippet, sender):
    return {"id": f"m{i}", "thread": f"t{i}", "date": date, "sender": sender, "subject": subject, "snippet": snippet, "to": ""}


def test_due_dates_from_the_wordings_assessment_emails_use():
    sent = "2026-09-24T15:30:43"
    assert assessments.due_from("You can get started at your convenience before September 27th, 12:30 pm PDT", sent) == "2026-09-27"
    assert assessments.due_from("Please complete the assessment by Sep 26, 2026, 14:00 UTC.", sent) == "2026-09-26"
    assert assessments.due_from("The deadline to take all parts of the assessment is September 25, 2026", sent) == "2026-09-25"
    assert assessments.due_from("the test will remain open for 30 days from the date it is received", sent) == "2026-10-24"
    assert assessments.due_from("The assessment will expire in 3 days.", sent) == "2026-09-27"
    assert assessments.due_from("Good luck with your assessment!", sent) is None


def test_company_is_named_in_full_never_a_stray_word(monkeypatch):
    monkeypatch.setattr(assessments, "_owner", lambda: ({"alex", "rivera"}, {"example.edu"}))
    companies = ["Capital One", "PDT Partners", "Chicago Trading Company", "🔥 ClipClop", "Vistara"]
    assert assessments.company_of("Final Reminder for Alex: ClipClop is waiting for your result on CodeSignal", "", "no-reply@codesignal.com", companies) == "ClipClop"
    assert assessments.company_of("Assessment Completed: ClipClop General Coding Assessment", "completed on September 5th, 10:30 pm PDT. Thanks for your time!",
                                  "no-reply@codesignal.com", companies) == "ClipClop"
    assert assessments.company_of("Thanks for taking the Vistara Beginner Coding Assessment (Intern)", "", "support@hackerrankforwork.com", companies) == "Vistara"
    assert assessments.company_of("Reminder: London Stock Exchange Group invites you to complete a Codility assessment", "", "no-reply@codility.com", companies) == "London Stock Exchange Group"


def test_scan_merges_reminders_marks_completions_and_skips_non_assessments(tmp_path, monkeypatch):
    monkeypatch.setattr(assessments, "_owner", lambda: ({"alex", "rivera"}, {"example.edu"}))
    state, _ = setup(tmp_path, [job(1, "Vistara"), job(2, "Ledgerly")])
    messages = [
        mail(1, "2026-09-18T17:42:49", "Next Step: Vistara Coding Assessment", "Your next step is to complete an online coding assessment", "vistara@myworkday.com"),
        mail(2, "2026-09-18T17:44:17", "Your HackerRank Vistara Beginner Coding Assessment (Intern)", "You have been invited", "support@hackerrankforwork.com"),
        mail(3, "2026-09-22T01:06:17", "Thanks for taking the Vistara Beginner Coding Assessment (Intern)", "Submission Confirmation", "support@hackerrankforwork.com"),
        mail(4, "2026-09-24T15:30:43", "Ledgerly invited you to take Ledgerly 2025 Coding Assessment on CodeSignal", "invited", "no-reply@codesignal.com"),
        mail(5, "2026-09-24T15:30:31", "Ledgerly invited you to take Ledgerly Frontend Challenge Assessment on CodeSignal", "invited", "no-reply@codesignal.com"),
        mail(6, "2026-09-24T15:30:18", "Ledgerly | Confirmation on your Application + CodeSignal", "Thank you for taking the time to apply", "no-reply@ashbyhq.com"),
        mail(7, "2026-09-20T10:00:00", "Reminder to complete SEC100: Security training", "complete by Oct 21", "training@example.edu"),
    ]
    bodies = {"t4": {"body": "The assessment will expire in 3 days. https://app.codesignal.com/test/abc"}}
    assessments.scan(state, search=lambda q, n: messages, bodies=lambda ids: {k: v for k, v in bodies.items() if k in ids})
    cards = {(c["company"], c["title"]): c for c in assessments.board(state)}
    vistara = [c for (co, _), c in cards.items() if co == "Vistara"]
    assert len(vistara) == 1 and vistara[0]["status"] == "done"          # invite + HackerRank invite + completion: one card
    ledgerly = sorted(t for (co, t) in cards if co == "Ledgerly")
    assert ledgerly == ["Ledgerly 2025 Coding Assessment", "Ledgerly Frontend Challenge Assessment"]  # two tests from one sender stay apart
    coding = cards[("Ledgerly", "Ledgerly 2025 Coding Assessment")]
    assert coding["due"] == "2026-09-27" and coding["link"] == "https://app.codesignal.com/test/abc"
    assert not any("SEC100" in t for (_, t) in cards)              # university training is not an assessment


def test_school_mail_is_recognized_behind_a_display_name_and_a_public_email_domain_filters_nothing(tmp_path, monkeypatch):
    from job_agent import paths
    home = tmp_path / "me"
    home.mkdir()
    monkeypatch.setenv("SPAM_HOME", str(home))
    (home / "profile.json").write_text(json.dumps({"facts": {"name": "Alex Rivera", "email": "alex@gmail.com",
                                                             "education": [{"school_domain": "example.edu"}]}}))
    assert assessments._owner()[1] == {"example.edu"}  # gmail.com is not the user's school
    state, _ = setup(tmp_path, [job(1, "Vistara")])
    messages = [
        mail(1, "2026-09-18T17:42:49", "Next Step: Vistara Coding Assessment", "complete an online coding assessment", "Vistara Recruiting <vistara.recruiting@example.com>"),
        mail(2, "2026-09-20T10:00:00", "Complete the coding assessment for CS 101", "assessment due", "Course Staff <staff@cse.example.edu>"),
    ]
    assessments.scan(state, search=lambda q, n: messages, bodies=lambda ids: {})
    titles = [c["title"] for c in assessments.board(state)]
    assert any("Vistara" in t for t in titles) and not any("CS" in t for t in titles)


def test_imap_messages_bring_their_own_body_for_due_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(assessments, "_owner", lambda: (set(), set()))
    state, _ = setup(tmp_path, [job(1, "Vistara")])
    message = {**mail(1, "2026-09-24T15:30:43", "Vistara invited you to take a Coding Assessment on CodeSignal", "invited", "no-reply@codesignal.com"),
               "thread": None, "body": "The assessment will expire in 3 days. https://app.codesignal.com/test/abc"}
    assessments.scan(state, search=lambda q, n: [message], bodies=lambda ids: {})
    [card] = assessments.board(state)
    assert card["due"] == "2026-09-27" and card["link"] == "https://app.codesignal.com/test/abc"
