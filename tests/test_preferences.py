from job_agent.preferences import with_relocation_preference
from job_agent.profile import Profile


def test_global_relocation_preference_does_not_change_current_residence():
    data=with_relocation_preference({'answers':{'Are you willing to relocate?':'No'},'facts':{'address':{'state':'Michigan'}}})
    profile=Profile(data)
    for question in ['Are you willing to relocate?', 'Are you open to relocating to Montpelier?',
                     'Are you located in or willing to relocate to New York?', 'Would you relocate to this office?']:
        assert profile.lookup(question,choice=True)=='Yes'
    for question in ['Do you currently live in New York?', 'Are you willing to relocate at your own expense?',
                     'Are you not willing to relocate?', 'Are you willing to relocate without relocation assistance?']:
        assert profile.lookup(question,choice=True) is None
    assert data['facts']['address']['state']=='Michigan'
    assert profile.lookup('Are you willing to relocate?')=='Yes'
