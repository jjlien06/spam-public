from job_agent import hand_submit


JOBS = [
    {"id": "a" * 20, "company": "Acme", "role": "SWE Intern", "note": "",
     "url": "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555/application?embed=true"},
    {"id": "b" * 20, "company": "Beta", "role": "ML Intern", "note": "",
     "url": "https://jobs.lever.co/beta/66666666-7777-8888-9999-000000000000"},
    {"id": "c" * 20, "company": "Gone", "role": "Data Intern", "note": "",
     "url": "https://jobs.ashbyhq.com/gone/abcdefab-cdef-abcd-efab-cdefabcdefab"},
]
TARGETS = [
    {"id": "T1", "type": "page", "url": "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555/application"},
    {"id": "T2", "type": "page", "url": "https://jobs.lever.co/beta/66666666-7777-8888-9999-000000000000/apply"},
    {"id": "T3", "type": "page", "url": "https://example.com/unrelated"},
    {"id": "T4", "type": "service_worker", "url": "https://jobs.ashbyhq.com/gone/abcdefab-cdef-abcd-efab-cdefabcdefab"},
]


def test_each_hand_submit_job_is_matched_to_its_open_window_by_posting_id():
    rows = hand_submit.match_windows(JOBS, TARGETS)
    assert [(r["company"], r["target"]) for r in rows] == [("Acme", "T1"), ("Beta", "T2"), ("Gone", None)]


def test_a_job_the_user_marked_submitted_is_listed_as_waiting_for_its_email():
    jobs = [dict(JOBS[0], note=hand_submit.USER_SUBMITTED + " 2026-10-11T15:00")]
    assert hand_submit.match_windows(jobs, TARGETS)[0]["user_submitted"] is True
