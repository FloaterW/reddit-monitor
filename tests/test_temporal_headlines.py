import pytest

from factual_checks import factual_issues


@pytest.mark.parametrize("title", ["Cardless available-credit hold through 11/10/2026", "Funds held until November 10"])
def test_latest_release_date_is_not_a_guaranteed_hold_duration(title):
    assert factual_issues(title, [{"body": "Your available credit should update on or before 11/10/2026."}])


@pytest.mark.parametrize("title,body", [
    ("Credit release expected by November 10", "Credit should update on or before 11/10/2026."),
    ("Funds held until November 10", "Funds will be held until November 10."),
    ("Funds held until November 9", "Funds should release by November 10."),
    ("Not necessarily held until November 10", "It should update on or before 11/10/2026."),
])
def test_qualified_or_explicitly_supported_headlines_remain_allowed(title, body):
    assert not factual_issues(title, [{"body": body}])


def test_conditional_support_process_cannot_become_completed_restoration():
    source = {"body": "If a microdeposit is held, transfers can be suspended. You have to call support, and they will eventually reenable external transfers after a few days."}
    assert factual_issues("The commenter called support before external transfers were restored after a few days.", [source])
    assert not factual_issues("The commenter warns that a microdeposit hold can suspend transfers and says calling support may restore them after a few days.", [source])


def test_actual_restoration_or_negative_outcome_is_not_blocked_by_prospective_language():
    source = {"body": "They said they would restore access later. I called and transfers were restored yesterday."}
    assert not factual_issues("Transfers were restored after calling.", [source])
    assert not factual_issues("Transfers were not restored.", [{"body": "Support said they would restore transfers."}])
