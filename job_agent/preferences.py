"""Explicit user preferences shared across applications."""
import re
from copy import deepcopy
from .profile import Profile

RELOCATION = r"\b(?:willing|able|prepared|open|agree)(?:\s+\w+){0,4}\s+(?:to\s+)?relocat(?:e|ing|ion)\b|\bwould you relocate\b"
RELOCATION_EXCEPTIONS = ["not willing", "not able", "unwilling", "unable", "assistance", "own expense", "without company support", "reimbursement"]


def with_relocation_preference(data):
    """Record willingness, without claiming a current location or funding agreement."""
    data=deepcopy(data)
    data.setdefault('facts',{})['willing_to_relocate']=True
    rule={'match':RELOCATION,'value':'Yes','unless_mentions':RELOCATION_EXCEPTIONS,
          'note':'User explicitly stated always willing to relocate across all applications.'}
    data['patterns']=[rule]+[r for r in data.get('patterns',[]) if r.get('match')!=RELOCATION]
    for label,entry in data.get('answers',{}).items():
        labels=[label]+(entry.get('aliases',[]) if isinstance(entry,dict) else [])
        if any(re.search(RELOCATION,text,re.I) and not any(e in text.lower() for e in RELOCATION_EXCEPTIONS) for text in labels):
            data['answers'][label]={**entry,'value':'Yes'} if isinstance(entry,dict) else 'Yes'
    Profile(data)
    return data
