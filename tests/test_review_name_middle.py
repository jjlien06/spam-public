from job_agent.review_check import check_review


def test_review_accepts_saved_middle_name_in_full_legal_name():
    profile = {"facts": {"middle_name": "Morgan"}, "answers": {"Name": "Alex Lee"}}

    assert check_review("Name\nAlex Morgan Lee", profile) == []
    assert check_review("Name\nAlex Taylor Lee", profile) == [
        '"Name" is answered "Alex Taylor Lee" but the profile says Alex Lee'
    ]
