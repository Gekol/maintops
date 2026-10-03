"""The eval's deterministic checks must catch wrong figures and skill claims without flagging correct ones."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from eval_checks import (  # noqa: E402
    CONFIRMATION,
    WRITE_CLAIM,
    misattributed_figures,
    numbers_in,
    prices_without_hourly_unit,
    skill_claim_errors,
    top_pick_errors,
    travel_mode_errors,
    ungrounded_numbers,
    values_in,
)

CANDIDATES = [
    {"handyman_id": 1, "name": "Sophie Brandt", "matched_skills": ["tile repair", "tile replacement", "floor tiling"],
     "success_rate_percent": 100, "jobs_of_this_type": 12, "travel_minutes": 6.9, "avg_hourly_rate_eur": 64.49,
     "skills_matched": "3 of 4"},
    {"handyman_id": 2, "name": "Klaus Schafer", "matched_skills": ["tile repair", "tile replacement", "floor tiling",
                                                                   "grouting"], "skills_matched": "4 of 4"},
]


def test_numbers_ignore_list_markers_and_rating_scale():
    text = ("1. Sign up\n"
            "2. Rate the job from 1 to 5, "
            "e.g. 4/5 stars.\n"
            "Incident #1000045 costs €64.49")
    assert numbers_in(text) == [4.0, 1000045.0, 64.49]          # "4/5": the 4 is a claim, the scale is not


def test_values_include_numbers_inside_strings():
    assert {3.0, 4.0, 2026.0, 9.0, 28.0} <= values_in({"skills_matched": "3 of 4", "completed_at": "2026-09-28"})


def test_grounded_figures_and_roundings_pass():
    allowed = values_in(CANDIDATES) | {1000045.0}
    reply = ("I have logged incident #1000045. "
             "Sophie Brandt: 100% success "
             "over 12 jobs, a 7-minute drive, "
             "about €64 per hour.")
    assert ungrounded_numbers(reply, allowed) == []


def test_invented_figures_fail():
    allowed = values_in(CANDIDATES)
    assert ungrounded_numbers("Sophie has a 95% success rate and costs €150.", allowed) == ["150", "95"]


def test_emergency_number_is_always_allowed():
    assert ungrounded_numbers("Call 112 first.", set()) == []


def test_all_skills_claim_needs_full_match():
    errors = skill_claim_errors("My top pick is Sophie Brandt. She matches all three required skills.", CANDIDATES, 4)
    assert errors and "3 of 4" in errors[0]
    assert skill_claim_errors("Klaus Schafer matches all the required skills.", CANDIDATES, 4) == []


def test_claims_about_all_candidates_check_each_one():
    assert skill_claim_errors("All three candidates match 3 of 4 required skills.", CANDIDATES, 4)   # Klaus has 4
    two_of_three = [{"handyman_id": 3, "name": "Anna Berg", "matched_skills": ["door repair", "carpentry"]}]
    assert skill_claim_errors("All three candidates match 2 of 3 required skills.", two_of_three, 3) == []


def test_x_of_y_claim_must_match_data():
    assert skill_claim_errors("Sophie Brandt matches 3 of 4 required skills.", CANDIDATES, 4) == []
    assert skill_claim_errors("Sophie Brandt matches 4 of 4 required skills.", CANDIDATES, 4)


def test_confirmation_and_write_claim_patterns():
    assert CONFIRMATION.match("Yes, please assign them.")
    assert not CONFIRMATION.match("The first one looks good.")
    assert WRITE_CLAIM.search("I've assigned Sophie Brandt to incident 5.")
    assert not WRITE_CLAIM.search("All your jobs are marked as completed.")


def test_candidates_sharing_a_first_name_do_not_break_the_parser():
    twins = [{"handyman_id": 7, "name": "Lukas Bauer", "matched_skills": ["a", "b"]},
             {"handyman_id": 8, "name": "Lukas Wolf", "matched_skills": ["a"]}]
    assert skill_claim_errors("Lukas matches all required skills.", twins, 2)


def test_full_name_wins_over_a_shared_first_name():
    jens = [{"handyman_id": 1, "name": "Jens Schmidt", "matched_skills": ["a", "b", "c"]},
            {"handyman_id": 2, "name": "Jens Klein", "matched_skills": ["a", "b", "c", "d"]}]
    assert skill_claim_errors("Jens Schmidt matches 3 of 4 required skills.", jens, 4) == []


def test_all_three_candidates_is_not_a_full_skill_claim():
    two_of_four = [{"handyman_id": n, "name": name, "matched_skills": ["a", "b"]}
                   for n, name in enumerate(["Andrea Baumann", "Ben Ott", "Cem Aras"])]
    reply = "My top pick is Andrea Baumann. All three match 2 of 4 required skills."
    assert skill_claim_errors(reply, two_of_four, 4) == []
    assert skill_claim_errors("Andrea Baumann matches all three required skills.", two_of_four, 4)


ELECTRICIANS = [
    {"handyman_id": 1, "name": "Milan Kuhne", "matched_skills": ["a", "b", "c"], "success_rate_percent": 96,
     "jobs_of_this_type": 104, "travel_minutes": 6.8},
    {"handyman_id": 2, "name": "Nina Esposito", "matched_skills": ["a", "b"], "success_rate_percent": 90,
     "jobs_of_this_type": 464, "travel_minutes": 3.1},
]


def test_only_one_who_covers_all_3_of_3_is_about_that_candidate():
    reply = "My top pick is Milan Kuhne, the only one who covers all 3 of 3 required skills."
    assert skill_claim_errors(reply, ELECTRICIANS, 3) == []


def test_another_candidates_figure_is_caught():
    reply = ("My top pick is **Milan Kuhne**: "
             "a 96% success rate across "
             "464 electrical jobs, "
             "a 7-minute drive away.")
    expected = ["Milan Kuhne: ['464'] are not this candidate's figures"]
    assert misattributed_figures(reply, ELECTRICIANS, set()) == expected
    own_figures = "Milan Kuhne: 96% over " "104 jobs, rated 4.51."
    assert misattributed_figures(own_figures, ELECTRICIANS, {4.51}) == []
    assert misattributed_figures("Milan Kuhne is closer than Nina (7 vs 3 minutes).", ELECTRICIANS, set()) == []


def test_prices_must_be_stated_per_hour():
    assert prices_without_hourly_unit("She charges about €68 per hour.") == []
    assert prices_without_hourly_unit("Her hourly rate is €68.") == []
    assert prices_without_hourly_unit("Rate: 68 €/h.") == []
    assert prices_without_hourly_unit("She is the most affordable at €68.") == ["€68"]


def test_travel_mode_must_match_the_handyman():
    cands = [{"handyman_id": 1, "name": "Nina Esposito", "travel_mode": "transit", "matched_skills": []},
             {"handyman_id": 2, "name": "Milan Kuhne", "travel_mode": "drive", "matched_skills": []}]
    assert travel_mode_errors("Nina Esposito is about 35 minutes away by public transport.", cands) == []
    assert travel_mode_errors("Milan Kuhne is a 7-minute drive away.", cands) == []
    assert travel_mode_errors("Nina Esposito is a 12-minute drive away.", cands) == [
        "Nina Esposito has no car but is described as driving"]


def test_the_other_two_means_the_remaining_candidates():
    cands = [{"handyman_id": 1, "name": "Tomasz Albrecht", "matched_skills": ["a", "b", "c"]},
             {"handyman_id": 2, "name": "Ben Ott", "matched_skills": ["a", "b"]},
             {"handyman_id": 3, "name": "Cem Aras", "matched_skills": ["a", "b"]}]
    reply = ("My top pick is Tomasz Albrecht. He covers 3 of 4 required skills. "
             "The other two candidates each match only 2 of 4 skills.")
    assert skill_claim_errors(reply, cands, 4) == []


def test_hourly_unit_anywhere_in_the_sentence():
    assert prices_without_hourly_unit("His hourly rate has averaged about €65 on past jobs.") == []
    assert prices_without_hourly_unit(
        "They are cheaper per hour (about €47 and €51 respectively vs. Andrea's ~€67).") == []


def test_a_name_in_the_sentence_beats_the_other_two():
    cands = [{"handyman_id": 1, "name": "Tomasz Albrecht", "matched_skills": ["a", "b", "c"]},
             {"handyman_id": 2, "name": "Ahmet Bohm", "matched_skills": ["a", "b", "c"]},
             {"handyman_id": 3, "name": "Susanne Becker", "matched_skills": ["a", "b"]}]
    reply = ("My top pick is Tomasz Albrecht, who covers 3 of 4 required skills. The other two are also solid: "
             "Ahmet Bohm is a 1-minute drive away, and Susanne Becker only matches 2 of 4 required skills.")
    assert skill_claim_errors(reply, cands, 4) == []
    assert skill_claim_errors(reply.replace("Susanne Becker only", "Ahmet Bohm only"), cands, 4)


def test_top_pick_must_be_the_first_candidate():
    cands = [{"handyman_id": 1, "name": "Susanne Becker", "matched_skills": []},
             {"handyman_id": 2, "name": "Christina Lewandowski", "matched_skills": []}]
    assert top_pick_errors("My top pick is **Susanne Becker**: 9 years of drains.", cands) == []
    assert top_pick_errors("My top pick is **Christina Lewandowski** — closest of the three.", cands) == [
        "top pick is Christina Lewandowski, but the first candidate is Susanne Becker"]
    assert top_pick_errors("Christina is also close by.", cands) == []


def test_candidates_referred_to_by_rank():
    cands = [{"handyman_id": 1, "name": "Jens Schmidt", "matched_skills": ["a", "b"]},
             {"handyman_id": 2, "name": "Ben Ott", "matched_skills": ["a", "b", "c"]},
             {"handyman_id": 3, "name": "Cem Aras", "matched_skills": ["a", "b", "d"]}]
    reply = ("My top pick is **Jens Schmidt**. Do note he matches 2 of 4 required skills, while candidates 2 and 3 "
             "each cover 3 of 4 if broader skill coverage is a priority for you.")
    assert skill_claim_errors(reply, cands, 4) == []
    assert skill_claim_errors(reply.replace("candidates 2 and 3 each cover 3", "candidate 2 covers 4"), cands, 4)
    assert skill_claim_errors("Jens Schmidt covers 2 of 4, the other two cover 3 of 4 skills.", cands, 4) == []
