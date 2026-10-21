"""Question labels shared by run reports and the local Answer Deck."""
import re


def question_label(value):
    return re.sub(r" \((?:saved answer not among options|did not stick|"
                  r"saved answer does not match a radio option|answer not an available option|"
                  r"boolean answer required|no text answer saved|required, nothing ticked)\)$", "", value).strip()


def is_diagnostic(question):
    return bool(re.search(
        r"^(?:Site error:|Could not fill this page|Navigation made|No unique supported|"
        r"Run crashed|Ambiguous submit|Unlabelled |Review |Required (?:resume|transcript)|"
        r"The site |Submit was|Not resubmitting:)|"
        r"has saved records but its Add section is still empty|"
        r"\b(?:password|security code|verification code|activation code|one.time code|OTP)\b",
        question, re.I))
