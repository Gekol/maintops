"""Runtime guard: figures stated for one handyman must be that handyman's own."""

from maintops_core.grounding import candidate_summary, misattributed_figures

# The candidates find_handymen returned when the eval caught Manny mixing them up (2026-10-02)
CANDIDATES = [
    {"handyman_id": 1, "name": "Nina Esposito", "match_percent": 91, "success_rate_percent": 96,
     "jobs_of_this_type": 464, "avg_rating": 4.63, "travel_minutes": 3.4, "skills_matched": "2 of 3"},
    {"handyman_id": 2, "name": "Milan Kuhne", "match_percent": 91, "success_rate_percent": 87,
     "jobs_of_this_type": 104, "avg_rating": 4.29, "travel_minutes": 6.6, "skills_matched": "3 of 3"},
    {"handyman_id": 3, "name": "Tobias Thomas", "match_percent": 88, "success_rate_percent": 88,
     "jobs_of_this_type": 102, "avg_rating": 4.26, "travel_minutes": 3.3, "skills_matched": "2 of 3"},
]
SHARED = {1001436.0, 3.0}


def test_another_candidates_figures_are_caught():
    reply = ("My top pick is **Milan Kuhne**, with a strong 96% success rate "
             "across 464 electrical jobs, a 7-minute drive away.")
    assert misattributed_figures(reply, CANDIDATES, SHARED) == ["Milan Kuhne: 96, 464"]


def test_correct_figures_and_comparisons_pass():
    reply = ("I have logged incident #1001436. My top pick is Milan Kuhne: "
             "87% success over 104 jobs, a 7-minute drive away. "
             "Nina Esposito has more experience than the others (464 jobs).")
    assert misattributed_figures(reply, CANDIDATES, SHARED) == []


def test_fallback_summary_is_grounded():
    summary = candidate_summary(1001436, CANDIDATES)
    assert "Nina Esposito" in summary and "96% success" in summary and "464 jobs" in summary
    assert misattributed_figures(summary, CANDIDATES, SHARED) == []
