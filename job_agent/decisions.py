"""Laya selects bounded actions; execution and permissions remain in browser code."""
from .models import Laya


class LayaDecider:
    def __init__(self, model=None, min_probability=0.75):
        self.model = model or Laya()
        self.min_probability = min_probability

    def choose(self, state, options):
        if not options or len(options) > 4 or len(set(options)) != len(options):
            return None
        try:
            result = self.model.predict(state, options)
            answer = result["answers"]["action"]
            selected = answer["choice"]
            probability = answer["probabilities"][selected]
            if selected not in options or not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
                return None
            if probability < self.min_probability:
                return None
            return options.index(selected)
        except (ValueError, KeyError, TypeError):
            return None
