import re


def normalize(label):
    return re.sub(r"\s+", " ", label.strip().lower().rstrip(" *:؟?."))


class Profile:
    def __init__(self, data):
        self.data = data
        self.answers = {}
        for label, answer in data.get("answers", {}).items():
            if isinstance(answer, dict):
                if "value" not in answer or answer["value"] is None:
                    continue
                labels = [label, *answer.get("aliases", [])]
                value = answer["value"]
            else:
                labels, value = [label], answer
            if value is None:
                continue
            for alias in labels:
                key = normalize(alias)
                if key in self.answers and self.answers[key] != value:
                    raise ValueError("Conflicting profile aliases: " + alias)
                self.answers[key] = value
        # Patterns answer whole families of questions ("previously employed at X?")
        # except when the question names one of the listed exceptions.
        self.patterns = [(re.compile(item["match"], re.IGNORECASE), item["value"],
                          [normalize(text) for text in item.get("unless_mentions", [])])
                         for item in data.get("patterns", []) if item.get("value") is not None]
        # Free-text boxes take a pattern only from this separate list of plain facts
        # (GPA, zip code, legal name), never from the choice patterns.
        self.text_patterns = [(re.compile(item["match"], re.IGNORECASE), item["value"],
                               [normalize(text) for text in item.get("unless_mentions", [])])
                              for item in data.get("text_patterns", []) if item.get("value") is not None]

    def lookup(self, label, choice=False):
        """Exact saved answer for a label; choice patterns only for choice controls
        (radio/select/listbox/checkbox); free text only from text_patterns."""
        key = normalize(label)
        if key in self.answers:
            return self.answers[key]
        if not choice:
            for pattern, value, exceptions in self.text_patterns:
                if pattern.search(key) and not any(text in key for text in exceptions):
                    return value
            return self._library(label, choice)
        for pattern, value, exceptions in self.patterns:
            if pattern.search(key):
                return None if any(text in key for text in exceptions) else value
        return self._library(label, choice)

    def _library(self, label, choice):
        """Last: a shared or private library rule, answered from the user's confirmed core facts."""
        if not self.data.get("core"):
            return None
        from .library import resolve
        return resolve(self.data, label, choice)

    def lookup_detail(self, label, choice=False):
        """Return an answer plus why it was selected, for audit logs/UI."""
        key = normalize(label)
        if key in self.answers:
            return {"value": self.answers[key], "confidence": "explicit_saved", "source": "answers"}
        if choice:
            for pattern, value, exceptions in self.patterns:
                if pattern.search(key) and not any(text in key for text in exceptions):
                    return {"value": value, "confidence": "approved_pattern", "source": "patterns"}
        return {"value": None, "confidence": "unknown", "source": None}


def audit_profile(data):
    """Validate the profile without contacting a job site or guessing answers."""
    issues = []
    answers = data.get("answers", {})
    if not isinstance(answers, dict):
        issues.append("answers must be an object")
        answers = {}
    for label, answer in answers.items():
        if not isinstance(label, str) or not label.strip():
            issues.append("answer has an empty question label")
        if isinstance(answer, dict):
            if answer.get("value") in (None, ""):
                issues.append("unresolved answer: " + str(label))
            if not isinstance(answer.get("aliases", []), list):
                issues.append("aliases must be a list: " + str(label))
        elif answer in (None, ""):
            issues.append("unresolved answer: " + str(label))
    try:
        Profile(data)
    except (TypeError, ValueError, re.error) as exc:
        issues.append(str(exc))
    for item in data.get("patterns", []):
        if item.get("value") in (None, ""):
            issues.append("pattern has no approved value")
        if not item.get("match"):
            issues.append("pattern has no match")
    return {"status": "verified" if not issues else "needs_review", "issues": sorted(set(issues)),
            "explicit_answers": sum(1 for value in answers.values() if value not in (None, "")),
            "approved_patterns": sum(1 for item in data.get("patterns", []) if item.get("value") not in (None, "")),
            "unresolved": data.get("unresolved", {})}
