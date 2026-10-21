from job_agent.form_controls import saved_value
from job_agent.profile import Profile


def test_choice_alias_from_a_pattern_does_not_conflict_with_the_exact_answer():
    # rubrik: "Graduation Date" (exact date) vs a choice pattern for "expected graduation" crashed the run.
    profile = Profile({"answers": {"Graduation Date": {"value": "2029-05-01", "aliases": []}},
                       "patterns": [{"match": "^expected graduation$", "value": [{"matches": "^may 2029$"}]}]})
    assert saved_value(profile, "Graduation Date", choice=True) == "2029-05-01"
