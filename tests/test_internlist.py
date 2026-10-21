import json

from job_agent import internlist
from job_agent.queue import Queue


def airtable(rows):
    """What the embed page and the shared-view data endpoint return."""
    columns = [{"id": f"fld{i}", "name": n} for i, n in enumerate(["Position Title", "Date", "Apply", "Location", "Company", "Salary", "Hire Time"])]
    ids = {c["name"]: c["id"] for c in columns}
    data = {"data": {"table": {"columns": columns, "rows": [{"cellValuesByColumnId": {ids[k]: v for k, v in r.items()}} for r in rows]}}}
    page = 'x urlWithParams: "\\u002Fv0.3\\u002Fview\\u002FviwX\\u002FreadSharedViewData?x=1" y'
    return page, json.dumps(data)


def test_roles_resolve_to_the_employers_posting_by_exact_title(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    queue.add([{"id": "a" * 20, "company": "Acme Corp", "role": "Old Role", "location": "", "platform": "greenhouse",
                "url": "https://job-boards.greenhouse.io/acme/jobs/1"},
               {"id": "b" * 20, "company": "Globex", "role": "Old", "location": "", "platform": "workday",
                "url": "https://globex.wd5.myworkdayjobs.com/en-US/Careers/job/NYC/Old_R1"}], "test")
    page, data = airtable([
        {"Position Title": "Software Engineer Intern", "Company": "Acme", "Date": "2026-09-24", "Location": "NYC",
         "Apply": {"label": "Apply", "url": "https://jobright.ai/jobs/info/1"}},
        {"Position Title": "Data Science Intern – Summer 2027", "Company": "Globex", "Date": "2026-09-23", "Location": "NYC"},
        {"Position Title": "ML Intern", "Company": "Initech", "Date": "2026-09-22", "Location": "Austin, TX",
         "Salary": "$40/hr", "Apply": {"label": "Apply", "url": "https://jobright.ai/jobs/info/3"}},
        {"Position Title": "Old Role", "Company": "Acme Corp", "Date": "2026-09-01"},          # already queued
    ])

    def fetch(url, data=None, headers=None):
        if "airtable.com/embed" in url:
            return page
        if "readSharedViewData" in url:
            return data_json
        if "boards-api.greenhouse.io/v1/boards/acme/jobs" in url:
            return json.dumps({"jobs": [{"title": "Software Engineer Intern", "absolute_url": "https://acme.com/careers?gh_jid=77", "id": 77},
                                        {"title": "Software Engineer Intern II", "absolute_url": "https://acme.com/careers?gh_jid=78", "id": 78}]})
        if "globex.wd5.myworkdayjobs.com/wday/cxs/globex/Careers/jobs" in url:
            return json.dumps({"jobPostings": [{"title": "Data Science Intern - Summer 2027", "externalPath": "/job/NYC/DS_R9"}]})
        raise OSError(url)
    data_json = data
    report = internlist.pull(queue, fetch=fetch, lists={"intern-list-swe": ("appA", "shrA", "Software Engineering")})
    urls = {r["url"] for r in queue.rows() if r["source"] == "intern-list-swe"}
    assert urls == {"https://job-boards.greenhouse.io/acme/jobs/77", "https://globex.wd5.myworkdayjobs.com/en-US/Careers/job/NYC/DS_R9"}
    assert report["intern-list-swe_added"] == 2 and report["intern-list-swe_unmatched"] == 1
    [left] = internlist.discovered(queue.db)
    assert (left["company"], left["salary"], left["link"]) == ("Initech", "$40/hr", "https://jobright.ai/jobs/info/3")
    assert internlist.pull(queue, fetch=fetch, lists={"intern-list-swe": ("appA", "shrA", "Software Engineering")})["intern-list-swe_added"] == 0


def test_a_title_with_two_matching_postings_is_not_guessed():
    index = {"acme": {("greenhouse", "acme")}}
    listing = lambda kind, board: [("SWE Intern", "https://x/1"), ("SWE Intern", "https://x/2")]
    assert internlist.resolve("Acme", "SWE Intern", index, listing) is None


def test_boards_are_discovered_by_company_name_and_cached(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    calls = []

    def fetch(url, data=None, headers=None):
        calls.append(url)
        if url == "https://boards-api.greenhouse.io/v1/boards/initech/jobs":
            return json.dumps({"jobs": [{"title": "ML Intern", "absolute_url": "https://initech.com/careers?gh_jid=5", "id": 5}]})
        raise OSError(url)
    index = internlist.discover_boards(queue.db, ["Initech, Inc."], fetch)
    assert index == {"initech": {("greenhouse", "initech")}}
    before = len(calls)
    internlist.discover_boards(queue.db, ["Initech, Inc."], fetch)
    assert len(calls) == before  # cached, not probed again


def test_lever_and_ashby_postings_open_their_application_form():
    from job_agent.workday import form_url
    posting = "https://jobs.lever.co/acme/11111111-2222-4333-8444-555555555555"
    assert form_url(posting) == posting + "/apply"
    assert form_url(posting + "/apply") == posting + "/apply"
    ashby = "https://jobs.ashbyhq.com/notary/cccccccc-4444-4555-8666-dddddddddddd"
    assert form_url(ashby) == ashby + "/application"
    assert form_url(ashby + "/application?embed=true") == ashby + "/application?embed=true"
    assert form_url("https://job-boards.greenhouse.io/a/jobs/1") == "https://job-boards.greenhouse.io/a/jobs/1"
