from job_agent import extensions


def write(ext_dir, name, body):
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / f"{name}.py").write_text(body)


def test_no_extensions_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    assert extensions.adjust_profile({"a": 1}, {"id": "x"}, None) == {"a": 1}
    assert extensions.modes() == [] and extensions.pick_filter({"id": "x"}, None)


def test_mode_extension_adjusts_only_in_its_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    write(tmp_path / "extensions", "portfolio",
          "MODE = 'portfolio'\n"
          "def adjust_profile(p, job):\n    return {**p, 'extra': 'portfolio.pdf'}\n"
          "def pick_filter(job):\n    return job.get('note', '').startswith('Portfolio mode')\n")
    assert extensions.modes() == ["portfolio"]
    assert extensions.adjust_profile({}, {}, "portfolio") == {"extra": "portfolio.pdf"}
    assert extensions.adjust_profile({}, {}, None) == {}
    assert extensions.pick_filter({"note": "Portfolio mode: required"}, "portfolio")
    assert not extensions.pick_filter({"note": ""}, "portfolio")
    assert extensions.mode_tagged({"note": "Portfolio mode: required"}) == "portfolio"


def test_a_broken_extension_is_skipped_not_fatal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    write(tmp_path / "extensions", "bad", "raise RuntimeError('boom')\n")
    assert extensions.load() == [] and "bad" in capsys.readouterr().err


def test_a_mode_job_is_never_picked_as_normal_even_without_its_extension(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    assert not extensions.pick_filter({"note": "Transcript mode: required upload"}, None)


def test_an_unknown_mode_is_an_error_not_the_normal_profile(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    with pytest.raises(extensions.UnknownMode):
        extensions.adjust_profile({}, {}, "portfolio")


def test_a_crashing_extension_hook_is_reported_not_fatal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    write(tmp_path / "extensions", "flaky", "MODE = 'flaky'\ndef claims(job):\n    raise KeyError('x')\n")
    assert extensions.mode_tagged({"note": ""}) is None and "flaky" in capsys.readouterr().err


def test_approve_refuses_a_job_filled_in_a_mode_not_installed(tmp_path, monkeypatch):
    import json
    from job_agent import chat_ui
    from job_agent.queue import Queue
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    queue = Queue(tmp_path / "jobs.sqlite3")
    queue.add([{"id": "a" * 20, "company": "Acme", "role": "SWE Intern", "url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "platform": "greenhouse", "location": "", "category": "", "age_days": 0, "locations": ""}], "test")
    queue.db.execute("UPDATE jobs SET status='review'"); queue.db.commit()
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / ("a" * 20 + ".json")).write_text(json.dumps({"mode": "portfolio"}))
    started = []
    monkeypatch.setattr(chat_ui.subprocess, "Popen", lambda *a, **k: started.append(a))
    report = chat_ui.approve(tmp_path, ["a" * 20])
    assert started == [] and report["started"] == 0 and "portfolio" in report["refused"]


def test_a_mode_pick_keeps_the_company_cap(tmp_path, monkeypatch):
    import json
    from job_agent import ops
    from job_agent.queue import Queue
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    write(tmp_path / "extensions", "ml", "MODE = 'ml'\ndef claims(job):\n    return 'ML' in job.get('role', '')\n")
    queue = Queue(tmp_path / "jobs.sqlite3")
    queue.add([{"id": f"{i:020d}", "company": "Acme", "role": f"ML Intern {i}", "url": f"https://job-boards.greenhouse.io/acme/jobs/{i}",
                "platform": "greenhouse", "location": "", "category": "", "age_days": 0, "locations": ""} for i in range(6)], "test")
    queue.db.execute("UPDATE jobs SET status='submitted' WHERE id IN (SELECT id FROM jobs LIMIT 4)"); queue.db.commit()
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"targeting": {}}))
    assert ops.pick(tmp_path, profile, count=5, mode="ml") == []
