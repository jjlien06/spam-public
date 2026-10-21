"""Compact, source-backed proposals for unknown questions. Never auto-save facts."""
import json
import re
from pathlib import Path

from .agent_client import AgentClient, AgentError, digest, private_json
from .custom_controls import option_match
from .profile import normalize

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"], "properties": {
    "answers": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["id", "answer", "sources", "reason"], "properties": {
            "id": {"type": "string"}, "answer": {"type": ["string", "boolean", "number", "null"]},
            "sources": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}}}}}
SECRET = re.compile(r"password|secret|token|credential|social.security|ssn|passport|account.number", re.I)
WORDS = re.compile(r"[a-z0-9]{3,}")
STOP = set("the and your you are for with this that have what please would our from can how tell about".split())


def words(text):
    return set(WORDS.findall(text.lower())) - STOP


def sources_for(profile, questions):
    sources = []
    def add(label, value):
        if value is None or SECRET.search(label):
            return
        if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 1800:
            sources.append({"id": digest([label, value])[:12], "label": label, "value": value})
    for label, entry in profile.get("answers", {}).items():
        add(label, entry.get("value") if isinstance(entry, dict) else entry)
    def walk(value, label):
        if isinstance(value, dict):
            for key, child in value.items():
                if not SECRET.search(str(key)):
                    walk(child, label + "." + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, label + "." + str(index))
        else:
            add(label, value)
    walk(profile.get("facts", {}), "facts")
    query = words(" ".join(q["question"] for q in questions))
    if query & {'live', 'living', 'reside', 'residence', 'relocate', 'relocation', 'regions', 'location'}:
        query |= {'address', 'state', 'city', 'relocate', 'relocation'}
    essay = any(not q["options"] and re.search(r"why|describe|tell us|experience|project|interest", q["question"], re.I) for q in questions)
    ranked = sorted(sources, key=lambda s: (-len(query & words(s["label"] + " " + str(s["value"]))) -
                    (2 if essay and re.search(r"experience|project|skill|education", s["label"], re.I) else 0), s["id"]))
    selected, size = [], 0
    for source in ranked:
        if not (query & words(source["label"] + " " + str(source["value"]))) and not essay:
            continue
        length = len(json.dumps(source))
        if size + length > 3200 or len(selected) >= 12:
            continue
        selected.append(source)
        size += length
    return selected


def suggest(deck, labels=None, client=None, retry=False):
    profile, questions, _ = deck.snapshot()
    if labels is not None:
        wanted = {normalize(label) for label in labels}
        questions = [q for q in questions if normalize(q["question"]) in wanted]
    client = client or AgentClient(deck.state)
    proposals = []
    for start in range(0, min(len(questions), client.config.max_calls_per_run * 3), 3):
        group = questions[start:start + 3]
        sources = sources_for(profile, group)
        compact = [{"id": digest(q["question"])[:12], "question": q["question"], "options": q["options"],
                    "companies": sorted({o["company"] for o in q["observations"]})} for q in group]
        guardrails = profile.get("drafting_guardrails", [])
        summary = deck.profile_path.parent / "summary.md"
        # Never silently truncate claim constraints. A large summary needs a curated
        # profile drafting_guardrails section before automated essay drafting.
        approved_summary = deck.profile_path.parent / "agent_summary.md"
        if summary.exists() and not approved_summary.exists():
            content = summary.read_text()
            if len(content) <= 2200:
                guardrails = [guardrails, content]
            elif any(not q["options"] for q in group):
                raise AgentError("summary.md exceeds the compact context budget. Add a reviewed short agent_summary.md with the resume facts and all claim guardrails.")
        if approved_summary.exists():
            content = approved_summary.read_text()
            if len(content) > 2200:
                raise AgentError("Keep agent_summary.md under 2200 characters; do not omit claim constraints.")
            guardrails = [profile.get("drafting_guardrails", []), content]
        payload = {"questions": compact, "sources": sources, "guardrails": guardrails,
                   "unresolved_fields": list(profile.get("unresolved", {})),
                   "instruction": "Suggest exact answers only. Cite source IDs for every non-null answer. For essays use at most 120 words. If facts are missing answer null and ask one concise question. Never infer a personal fact from the job requirements or question wording."}
        by_id = {q["id"]: q for q in compact}
        source_ids = {s["id"] for s in sources}
        def validate(result):
            if not isinstance(result, dict) or set(result) != {"answers"} or not isinstance(result["answers"], list):
                raise AgentError("Invalid answer proposal schema.")
            seen = set()
            for answer in result["answers"]:
                if not isinstance(answer, dict) or set(answer) != {"id", "answer", "sources", "reason"}:
                    raise AgentError("Invalid answer proposal fields.")
                key, value = answer["id"], answer["answer"]
                if key not in by_id or key in seen:
                    raise AgentError("Agent returned an unknown or duplicate question.")
                seen.add(key)
                if not isinstance(answer["reason"], str) or len(answer["reason"]) > 1000 or not isinstance(answer["sources"], list):
                    raise AgentError("Invalid proposal explanation.")
                if any(s not in source_ids for s in answer["sources"]) or (value is not None and not answer["sources"]):
                    raise AgentError("The proposal lacks valid approved source references.")
                if value is not None and (type(value) not in (str, bool, int, float) or len(str(value)) > 1800):
                    raise AgentError("Answer exceeded the allowed format or length.")
                options = by_id[key]["options"]
                if value is not None and options and option_match([{"label": o, "value": o} for o in options], value) is None:
                    raise AgentError("Suggested answer does not match captured options.")
            if seen != set(by_id):
                raise AgentError("Agent omitted questions.")
        if not sources:
            result = {"result": {"answers": [{"id": q["id"], "answer": None, "sources": [],
                "reason": "No relevant approved fact is saved. Please provide this answer."} for q in compact]}, "cached": True}
        else:
            result = client.request("answer_questions", payload, SCHEMA, validate, retry=retry)
        for answer in result["result"]["answers"]:
            proposal = {**answer, "question": by_id[answer["id"]]["question"], "cached": result["cached"],
                        "model": client.config.model, "needs_review": True,
                        "evidence": [s for s in sources if s["id"] in answer["sources"]],
                        "profile_revision": digest(profile)}
            proposals.append(proposal)
    private_json(Path(deck.state) / "agent" / "suggestions.json", proposals)
    return {"proposals": proposals, "calls": client.calls, "remaining_questions": max(0, len(questions) - len(proposals))}
