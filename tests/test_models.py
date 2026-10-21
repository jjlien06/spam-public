import json

from job_agent.models import LocalWriter, drafting_context


def test_drafting_context_keeps_usable_material_and_excludes_metadata_and_conflicts():
    profile = {
        "source": "private import metadata",
        "facts": {"name": "Alex", "experience": [{"company": "Example"}]},
        "answers": {
            "Email": {"value": "alex@example.com", "aliases": ["Email address"]},
            "Phone": {"value": None, "aliases": ["Mobile"]},
            "GPA": "3.8",
        },
        "unresolved": {"sponsorship": "old conflicting claim", "phone": None},
        "drafting_guardrails": ["Do not infer eligibility"],
        "mailbox": {"email": "alex@example.com", "connected": True},
        "documents": {"Resume": "/private/resume.pdf"},
    }

    context = drafting_context(profile)

    assert context["facts"] == profile["facts"]
    assert context["answers"] == {"Email": "alex@example.com", "GPA": "3.8"}
    assert context["unresolved_fields"] == ["sponsorship", "phone"]
    assert context["drafting_guardrails"] == ["Do not infer eligibility"]
    serialized = json.dumps(context)
    assert "old conflicting claim" not in serialized
    assert "private import metadata" not in serialized
    assert "resume.pdf" not in serialized
    assert "connected" not in serialized


def test_local_writer_forces_review_and_sends_reduced_context(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data)
        return Response()

    def fake_json_load(_response):
        return {"message": {"content": json.dumps({"answer": "draft", "needs_review": False})}}

    monkeypatch.setattr("job_agent.models.urlopen", fake_urlopen)
    monkeypatch.setattr("job_agent.models.json.load", fake_json_load)
    profile = {
        "facts": {"name": "Alex"},
        "answers": {"Email": {"value": "alex@example.com"}},
        "unresolved": {"eligibility": "historical answer"},
        "drafting_guardrails": ["Review before use"],
        "mailbox": {"connected": True},
        "documents": {"Resume": "/private/resume.pdf"},
    }

    result = LocalWriter().draft("Describe your experience", profile)

    assert result == {"answer": "draft", "needs_review": True}
    assert captured["timeout"] == 180
    sent = captured["payload"]["messages"][1]["content"]
    assert "Alex" in sent and "alex@example.com" in sent
    assert "historical answer" not in sent
    assert "resume.pdf" not in sent
    assert "connected" not in sent
    assert captured["payload"]["options"] == {"num_ctx": 4096, "num_predict": 512}
