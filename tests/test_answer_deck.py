import json
import re
import threading
from http.client import HTTPConnection

import pytest

from job_agent.answer_deck import AnswerDeck, make_server, read_questions
from job_agent.profile import Profile
from job_agent.queue import Queue


def setup_deck(tmp_path, data=None):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(data or {"answers": {}, "facts": {"keep": "private"}}))
    queue = Queue(tmp_path / "jobs.sqlite3")
    (tmp_path / "runs").mkdir()
    return AnswerDeck(tmp_path, profile), queue, profile


def add_question(queue, state, job_id, question, options, company="Acme", status="needs_input", runtime="needs_input"):
    queue.ingest([{"id": job_id, "company": company, "role": "Software Intern", "location": "Remote",
                   "url": f"https://example.test/jobs/{job_id}", "platform": "generic"}])
    queue.mark(job_id, status)
    report = {"status": runtime, "missing": [question], "options": {question: options}}
    (state / "runs" / (job_id + ".json")).write_text(json.dumps(report))


def test_deck_keeps_each_sites_options_and_ignores_applied_and_code_runs(tmp_path):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, "a", "Are you available?", ["Yes", "No"])
    add_question(queue, tmp_path, "b", "Are you available?", ["Yes, immediately", "No"], "Beta")
    add_question(queue, tmp_path, "c", "Already sent?", ["Yes", "No"], status="submit_uncertain")
    add_question(queue, tmp_path, "d", "Enter security code", [], runtime="email_code_required")
    questions, issues = read_questions(tmp_path)
    assert len(questions) == 1
    assert questions[0]["options"] == ["Yes", "No", "Yes, immediately"]
    assert len(questions[0]["observations"]) == 2
    assert issues[0]["job_id"] == "d"
    preview = deck.preview({"kind": "exact", "question": "Are you available?", "value": "Yes"})
    assert preview["can_save"]
    assert [r["selected"] for r in preview["rows"]] == ["Yes", "Yes, immediately"]


def test_exact_preview_updates_aliases_only_when_saved_and_is_private(tmp_path):
    original = {"answers": {"Availability": {"value": "No", "aliases": ["Are you available?"]}}, "facts": {"keep": "private"}}
    deck, queue, profile = setup_deck(tmp_path, original)
    add_question(queue, tmp_path, "a", "Are you available?", ["Yes", "No"])
    preview = deck.preview({"kind": "exact", "question": "Are you available?", "value": "Yes"})
    assert json.loads(profile.read_text()) == original
    assert set(preview["affected_aliases"]) == {"availability", "are you available"}
    deck.save(preview["preview"])
    updated = json.loads(profile.read_text())
    assert updated["facts"] == original["facts"]
    assert Profile(updated).lookup("Availability") == "Yes"
    assert profile.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="Preview"):
        deck.save(preview["preview"])


@pytest.mark.parametrize("changed", ["profile", "report", "company_history"])
def test_stale_preview_cannot_overwrite_profile_or_ignore_changed_questions(tmp_path, changed):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, "a", "Are you available?", ["Yes", "No"])
    preview = deck.preview({"kind": "exact", "question": "Are you available?", "value": "Yes"})
    if changed == "profile":
        profile.write_text(json.dumps({"answers": {"Other": "keep"}}))
    elif changed == "report":
        add_question(queue, tmp_path, "a", "Are you available?", ["Later", "No"])
    else:
        add_question(queue, tmp_path, "b", "Other", [], status="submitted")
    before = profile.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        deck.save(preview["preview"])
    assert profile.read_bytes() == before


def test_rule_preview_respects_first_match_exceptions_and_choice_only(tmp_path):
    deck, queue, profile = setup_deck(tmp_path, {"answers": {}, "patterns": [
        {"match": "18", "value": "No", "unless_mentions": ["older"]}]})
    add_question(queue, tmp_path, "a", "Are you 18 or older?", ["Yes", "No"])
    add_question(queue, tmp_path, "b", "Are you under 18?", ["Yes", "No"])
    add_question(queue, tmp_path, "c", "Describe being 18", [])
    preview = deck.preview({"kind": "pattern", "match": "18", "value": "Yes"})
    assert not preview["can_save"]
    assert [r["after"] for r in preview["rows"]] == [None, "No", None]
    assert "preview" not in preview


def test_rule_preview_tests_real_options_and_can_append_a_narrow_rule(tmp_path):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, "a", "Are you 18 or older?", ["Yes", "No"])
    add_question(queue, tmp_path, "b", "Are you under 18?", ["Yes", "No"])
    add_question(queue, tmp_path, "c", "Describe your availability", [])
    invalid = deck.preview({"kind": "pattern", "match": "18", "value": "Maybe"})
    assert not invalid["can_save"]
    preview = deck.preview({"kind": "pattern", "match": "18 or older", "value": "Yes"})
    assert len(preview["rows"]) == 1 and preview["can_save"]
    deck.save(preview["preview"])
    saved = Profile(json.loads(profile.read_text()))
    assert saved.lookup("Are you 18 or older?", choice=True) == "Yes"
    assert saved.lookup("Are you under 18?", choice=True) is None
    assert saved.lookup("Are you 18 or older?", choice=False) is None


def test_preview_uses_the_same_company_history_as_the_batch(tmp_path):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, "a", "Have you previously applied here?", ["Yes", "No"])
    add_question(queue, tmp_path, "b", "Other", [], status="submitted")
    assert deck.view()["questions"][0]["current"] == "Yes"
    preview = deck.preview({"kind": "pattern", "match": "previously applied", "value": "No"})
    assert preview["rows"][0]["after"] == "Yes"
    assert not preview["can_save"]


def test_diagnostics_cannot_be_saved_as_profile_answers(tmp_path):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, "a", "Site error: Try again", [])
    assert not deck.view()["questions"]
    with pytest.raises(ValueError, match="current deck"):
        deck.preview({"kind": "exact", "question": "Site error: Try again", "value": "Yes"})


def test_preview_catches_conflicts_with_builtin_address_aliases(tmp_path):
    deck, queue, profile = setup_deck(tmp_path, {"answers": {"Postal code": "10001"}})
    add_question(queue, tmp_path, "a", "Zip code", [])
    assert deck.view()["questions"][0]["current"] == "10001"
    with pytest.raises(ValueError, match="Conflicting saved answers"):
        deck.preview({"kind": "exact", "question": "Zip code", "value": "10002"})
    assert json.loads(profile.read_text())["answers"] == {"Postal code": "10001"}


@pytest.fixture
def local_deck(tmp_path):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, "a", "Are you available?", ["Yes", "No"], "Acme <img src=x onerror=alert(1)>")
    server = make_server(deck, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, deck, profile
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        queue.db.close()


def test_local_http_rejects_foreign_origins_missing_token_and_file_paths(local_deck):
    server, deck, profile = local_deck
    conn = HTTPConnection("127.0.0.1", server.server_port)
    conn.request("GET", "/", headers={"Host": "attacker.test"})
    response = conn.getresponse(); assert response.status == 403; response.read()
    conn.request("GET", "/api/deck", headers={"Origin": "https://attacker.test"})
    response = conn.getresponse(); assert response.status == 403; response.read()
    conn.request("GET", "/")
    response = conn.getresponse(); html = response.read().decode()
    assert "no-store" == response.getheader("Cache-Control")
    token = re.search(r"const token = '([^']+)'", html).group(1)
    body = json.dumps({"kind": "exact", "question": "Are you available?", "value": "Yes"})
    conn.request("POST", "/api/preview", body, {"Content-Type": "application/json"})
    response = conn.getresponse(); assert response.status == 403; response.read()
    conn.request("POST", "/api/preview", body, {"Content-Type": "application/json", "X-Deck-Token": token})
    response = conn.getresponse(); assert response.status == 200
    assert json.loads(response.read())["can_save"]
    conn.request("GET", "/profile.json")
    response = conn.getresponse(); assert response.status == 404; response.read()
    conn.close()


def test_answer_deck_browser_previews_and_saves_without_rendering_site_html(local_deck):
    from playwright.sync_api import sync_playwright
    server, deck, profile = local_deck
    queue = Queue(deck.state / "jobs.sqlite3")
    add_question(queue, deck.state, "b", "When can you start?", [], "Beta")
    queue.db.close()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        page.get_by_role("button", name="Yes", exact=True).click()
        page.get_by_role("button", name="Save to profile").wait_for()
        assert Profile(json.loads(profile.read_text())).lookup("Are you available?") is None
        assert page.locator("img").count() == 0
        page.get_by_role("button", name="Save to profile").click()
        page.get_by_text("Saved to the profile. Refill the affected jobs, then review them.", exact=True).wait_for()
        assert Profile(json.loads(profile.read_text())).lookup("Are you available?") == "Yes"
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#questions").get_by_label("Verified answer", exact=True).fill("June")
        page.get_by_role("button", name="Preview exact answer").click()
        page.get_by_role("button", name="Save to profile").wait_for()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        after = page.locator('td[data-label="After saving"]')
        assert after.inner_text() == "June"
        box = after.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        page.locator("#questions").get_by_label("Verified answer", exact=True).fill("July")
        assert page.locator("#preview").is_hidden()
        browser.close()


def test_native_select_and_radio_missing_options_reach_the_report():
    from playwright.sync_api import sync_playwright
    from job_agent.runner import prepare
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content('''<form>
          <label for="location">Preferred location</label><select id="location" required>
          <option value="">Select one</option><option>Remote</option><option>Office</option></select>
          <fieldset><legend>Available next summer?</legend>
          <label><input name="available" type="radio" required value="yes">Yes</label>
          <label><input name="available" type="radio" required value="no">No</label></fieldset>
          <button type="submit">Submit</button></form>''')
        report = prepare(page, {"answers": {}})
        assert report["status"] == "needs_input"
        assert report["options"] == {"Preferred location": ["Remote", "Office"], "Available next summer?": ["Yes", "No"]}
        browser.close()
