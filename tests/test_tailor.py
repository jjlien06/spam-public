import json
import pytest

from job_agent import tailor

SAMPLE = r"""\section{Projects}
\project{Alpha}{Python}{2026}
\begin{bullets}
  \item Built a parser in Python that is 3x faster.
  \item Wrote 12 tests with pytest.
\end{bullets}

\project{Beta}{C}{2025}
\begin{bullets}
  \item Built a cache simulator in C.
\end{bullets}

\section{Technical Skills}
\skill{Languages}{Python, C, Java}
"""


def test_parse_and_identity_render():
    parsed = tailor.parse(SAMPLE)
    assert parsed["blocks"] == [["Built a parser in Python that is 3x faster.", "Wrote 12 tests with pytest."],
                                ["Built a cache simulator in C."]]
    assert len(parsed["projects"]) == 2 and parsed["skills"] == {"Languages": ["Python", "C", "Java"]}
    plan, notes = tailor.validate({}, SAMPLE)
    assert tailor.parse(tailor.render(SAMPLE, plan))["blocks"] == parsed["blocks"] and not notes


def test_reorder_projects_bullets_skills():
    plan, _ = tailor.validate({"project_order": [1, 0],
                               "bullets": {"0": [{"src": 1, "text": "Wrote 12 tests with pytest."}, {"src": 0}]},
                               "skills": {"Languages": ["C", "Python", "Java"]}}, SAMPLE)
    out = tailor.render(SAMPLE, plan)
    assert out.index("Beta") < out.index("Alpha")
    assert out.index("12 tests") < out.index("3x faster")
    assert r"\skill{Languages}{C, Python, Java}" in out


@pytest.mark.parametrize("text,why", [
    ("Built a parser in Python that is 10x faster.", "numbers"),
    ("Built a parser in Rust that is 3x faster.", "terms"),
    ("Led a parser team in Python that is 3x faster.", "words"),
])
def test_gate_reverts_invented_claims(text, why):
    plan, notes = tailor.validate({"bullets": {"0": [{"src": 0, "text": text}, {"src": 1}]}}, SAMPLE)
    assert plan["bullets"][0][0]["text"] == "Built a parser in Python that is 3x faster."
    assert why in notes[0]


def test_gate_allows_grounded_rewording():
    text = "Built a 3x faster Python parser."
    plan, notes = tailor.validate({"bullets": {"0": [{"src": 0, "text": text}, {"src": 1}]}}, SAMPLE)
    assert plan["bullets"][0][0]["text"] == text and not notes


def test_structure_changes_are_refused():
    with pytest.raises(tailor.TailorError):
        tailor.validate({"bullets": {"0": [{"src": 0}]}}, SAMPLE)  # dropped a bullet
    with pytest.raises(tailor.TailorError):
        tailor.validate({"skills": {"Languages": ["Python", "C", "Rust"]}}, SAMPLE)


def test_apply_only_when_approved(tmp_path):
    folder = tailor.tailored_dir(tmp_path, "j1")
    folder.mkdir(parents=True)
    pdf = folder / "resume.pdf"
    pdf.write_bytes(b"%PDF")
    (folder / "meta.json").write_text(json.dumps({"approved": False, "pdf": str(pdf)}))
    profile = {"documents": {"Resume": "/d.pdf", "Attach resume": "/d.pdf", "Transcript": "/t.pdf"}}
    assert tailor.apply(profile, {"id": "j1"}, tmp_path) is None and profile["documents"]["Resume"] == "/d.pdf"
    tailor.approve(tmp_path, "j1")
    assert tailor.apply(profile, {"id": "j1"}, tmp_path) == str(pdf)
    assert profile["documents"] == {"Resume": str(pdf), "Attach resume": str(pdf), "Transcript": "/t.pdf"}
