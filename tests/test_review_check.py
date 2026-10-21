from job_agent.review_check import check_review

PROFILE = {
    "answers": {
        "Phone": {"value": "(248) 555-0100", "aliases": []},
        "Graduation Date": {"value": "2029-05-01", "aliases": ["What is your anticipated graduation date?"]},
        "Please indicate the shifts you are available to work": {"value": ["1st", "Days"], "aliases": []},
        "Desired salary": {"value": ["Open to discussion", {"matches": "^\\$?60,?000"}], "aliases": []},
        "Email": {"value": "student@example.edu", "aliases": ["Email Address"]},
    },
    "patterns": [
        {"match": "18 years or older", "value": "Yes"},
        {"match": "internship at (?!an organization)", "value": "No"},
        {"match": "require[^?]{0,60}sponsorship", "value": "No"},
        {"match": "expected (?:degree )?completion", "value": [{"matches": "may 2029"}]},
    ],
    "facts": {"education": [{"major_synonyms": ["Computer Science", "Computer and Information Science"]}]},
    "review_checks": [
        {"match": "(?:legally )?authori[sz]ed to work", "answer": "^yes\\b"},
        {"match": "(?:relatives?|family members?)[^?]{0,80}(?:employ|work)", "answer": "^no\\b|^not to my knowledge"},
        {"match": "18 (?:years )?(?:of age |old )?(?:or older)|at least 18", "answer": "^yes\\b"},
        {"match": "(?:previously|ever)[^?]{0,40}(?:employed|intern(?:ed|ship)?)[^?]{0,40}\\b(?:at|for|by)\\b (?!an? (?:organization|company)\\b)",
         "answer": "^no\\b|never"},
    ],
}

CLEAN = """Review
My Information
Email Address
someone.else@gmail.com
Phone
+1 (248) 5550100 (Mobile)
Education
Field of Study
Computer and Information Science
Skills
Resume/CV
Application Questions 1 of 2
Are you 18 years or older? If you are under 18, hire is subject to verification.*
Yes
Have you previously completed an internship at an organization?*
Yes
Have you previously completed an internship at Acme?*
No
Will you now or in the future require sponsorship?*
No
Please indicate the shifts you are available to work*
1st
Days
Desired salary*
Open to discussion
What is your anticipated graduation date?*
05/01/2029
What is your expected degree completion date?
Your expected graduation date should reflect the date you expect to complete all planned education, including any graduate degree you plan to pursue right after.
If you do not know the exact date, choose the first day of the month.*
05/01/2029
Base salary expectation per year.
No Response
3. Are you currently legally authorized to work for Acme on a full-time basis?
If this role is located in the United States, answer ‘Yes/No’ based on your U.S. work authorization.
Yes
9. To the best of your knowledge, is any member of your immediate family employed by Acme?
Not to my knowledge
Email
Have you previously worked for Acme? If yes, list your manager
No
Voluntary Disclosures
I decline to self-identify
Terms and Conditions
"""


def test_clean_review_page_passes():
    assert check_review(CLEAN, PROFILE) == []


def test_portfolio_warning_requires_link_or_uploaded_portfolio():
    # Redacted control snapshot from Printworks' optional Greenhouse fields.
    portfolio = ("Website/Portfolio (your application will not be considered without a portfolio linked here or attached below.):\n"
                 "No Response\n"
                 "If your portfolio is not available online, please attach it here.:\n"
                 "No Response\n")
    assert any("required by its label" in problem for problem in check_review(portfolio, {}))
    assert check_review(portfolio.replace("No Response\nIf your portfolio", "https://example.com/portfolio\nIf your portfolio"), {}) == []
    attached = portfolio.replace("If your portfolio is not available online, please attach it here.:\nNo Response",
                                 "If your portfolio is not available online, please attach it here.:\nportfolio.pdf")
    assert check_review(attached, {}) == []


def test_clause_flipped_wrong_answers_are_caught():
    wrong = (CLEAN.replace("verification.*\nYes", "verification.*\nNo")
             .replace("internship at Acme?*\nNo", "internship at Acme?*\nYes")
             .replace("sponsorship?*\nNo", "sponsorship?*\nYes")
             .replace("work authorization.\nYes", "work authorization.\nNo"))
    problems = check_review(wrong, PROFILE)
    assert any("18 years or older" in p for p in problems)
    assert any("internship at Acme" in p for p in problems)
    assert any("sponsorship" in p for p in problems)
    assert any("legally authorized" in p for p in problems)  # the answer after an instruction line
    assert not any("an organization" in p for p in problems)


def test_wrong_date_phone_and_salary_are_caught():
    wrong = (CLEAN.replace("graduation date?*\n05/01/2029", "graduation date?*\n12/01/2012")
             .replace("5550100 (Mobile)", "5550199 (Mobile)")
             .replace("Desired salary*\nOpen to discussion", "Desired salary*\n$200,000+"))
    problems = check_review(wrong, PROFILE)
    assert len(problems) == 3, problems


def test_graduation_month_year_text_gets_both_parts():
    from job_agent.form_controls import field_value
    from job_agent.profile import Profile
    profile = Profile({"answers": {"Graduation Date": "2029-05-01"}})
    assert field_value(profile, "When is your prospective Graduation Date? (Month/Year)") == "05/2029"
    assert field_value(profile, "Graduation year") == "2029"
    assert field_value(profile, "Graduation month") == "05"


def test_examples_line_is_not_the_answer():
    text = ("8. Is any member of your family a current Acme employee?\n"
            "Examples include any child, stepchild, parent, spouse or sibling, and any person sharing a household.\n"
            "Not to my knowledge\n")
    profile = {**PROFILE, "patterns": PROFILE["patterns"] + [{"match": "family[^?]*employee", "value": ["No", "Not to my knowledge"]}]}
    assert check_review(text, profile) == []
    assert check_review(text.replace("Not to my knowledge", "Yes"), profile)


def test_text_patterns_answer_free_text_but_choice_patterns_never_do():
    from job_agent.profile import Profile
    profile = Profile({"answers": {}, "patterns": [{"match": "gpa", "value": "3.5 or above"}],
                       "text_patterns": [{"match": "\\bgpa\\b", "value": "3.8", "unless_mentions": ["major gpa"]}]})
    assert profile.lookup("What is your current cumulative GPA? (Note: minimum 3.5)") == "3.8"
    assert profile.lookup("What is your current cumulative GPA?", choice=True) == "3.5 or above"
    assert profile.lookup("What is your major GPA?") is None


def test_free_text_answer_is_accepted_when_a_choice_rule_also_matches():
    profile = {"answers": {}, "patterns": [{"match": "gpa", "value": "Yes"}], "text_patterns": [{"match": "\\bgpa\\b", "value": "3.8"}]}
    text = "What is your current cumulative GPA? (Note: A minimum GPA of 3.5 is required.)*\n3.8\n"
    assert check_review(text, profile) == []
    assert check_review(text.replace("3.8\n", "2.9\n"), profile)


def test_resume_in_a_cover_letter_slot_is_flagged():
    assert check_review("Resume:\nAlex_Rivera_Resume.pdf\nCover Letter:\nAlex_Rivera_Resume.pdf\n", PROFILE)
    assert check_review("Resume:\nAlex_Rivera_Resume.pdf\nCover Letter:\nNo Response\n", PROFILE) == []


def test_resume_in_combined_attachment_field_is_accepted():
    text = ('"Resume / CV / Cover Letter / Other Attachments"\n'
            'Alex_Rivera_Resume.pdf successfully uploaded\n')
    assert check_review(text, PROFILE) == []


def test_a_text_answer_containing_a_semicolon_is_not_split():
    profile = {"answers": {"What is your desired salary?": ["Negotiable based on the role."]}}
    assert check_review("What is your desired salary?:\nNegotiable based on the role.\n", profile) == []


def test_work_experience_location_is_not_the_home_location():
    profile = {**PROFILE,
               "answers": {**PROFILE["answers"], "Location": {"value": ["Springfield, IL"], "aliases": []}},
               "facts": {**PROFILE["facts"], "experience": [{"company": "Acme", "location": "Riverton, CA, USA"}]}}
    page = "Work Experience\nJob Title\nIntern\nCompany\nAcme\nLocation\nRiverton, CA, USA\n"
    assert check_review(page, profile) == []
    assert check_review("Location\nShelbyville, TX\n", profile)


def test_oregon_education_disclaimer_between_question_and_answer_is_ignored():
    profile = {"patterns": [{"match": "upcoming school year.*academic year", "value": [{"matches": "^Undergraduate Senior$"}]}]}
    page = ("For the upcoming school year, which academic year will you be in?\n"
            "Applicants living in Oregon or applying for jobs in Oregon: Do not provide dates for when you attended or graduated from any educational institution\n"
            "Undergraduate Senior\n")
    assert check_review(page, profile) == []


def test_saved_exact_answer_missing_from_review_is_flagged():
    question = "What coding language do you feel most comfortable with?"
    profile = {"answers": {question: {"value": "Python"}}}
    assert check_review(f"{question}\nNo Response\n", profile) == [
        f'"{question}" has no response despite a saved answer']


def test_bulleted_question_continuation_is_not_the_answer():
    # HPE: the question's "•" conditions and country list follow its first line before the answer.
    text = """For any of the countries listed below:
• Are you a citizen or have a passport, or
• Have dual citizenship status, or
• Have been granted a permanent resident status, or
• Have been granted refugee or asylum status?
Armenia, Azerbaijan, Belarus, Cuba, Iran, North Korea
NO"""
    profile = {"answers": {}, "patterns": [{"match": "^for any of the countries listed below", "value": [{"matches": "^no$"}]}]}
    assert check_review(text, profile) == []
    assert check_review(text.replace("\nNO", "\nYES"), profile) != []


def test_workday_footer_privacy_link_is_not_a_question():
    # HCSC: the footer link "Privacy Statement" matches a saved exact answer; the copyright line is not its answer.
    profile = {**PROFILE, "answers": {**PROFILE["answers"],
                                      "Privacy Statement": {"value": [{"matches": "^i agree$"}], "aliases": []}}}
    text = "Terms and Conditions\nI accept the Terms\nYes\nBack\nSubmit\nFollow Us\nPrivacy Statement\n© 2026 Workday, Inc. All rights reserved."
    assert check_review(text, profile) == []


def test_category_prefixed_choice_matches_the_saved_plain_choice():
    # KLA's Workday review lists a pick made inside a category as "Social Network: Linkedin".
    profile = {"answers": {"How Did You Hear About Us?": {"value": ["LinkedIn"], "aliases": []}}}
    page = "Review\nMy Information\nHow Did You Hear About Us?\nSocial Network: Linkedin\nTerms and Conditions\n"
    assert check_review(page, profile) == []
    wrong = page.replace("Social Network: Linkedin", "Social Network: Facebook")
    assert any("How Did You Hear About Us?" in problem for problem in check_review(wrong, profile))
