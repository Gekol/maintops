"""'I'm on my way': travel mode rules and the client's arrival estimate."""

from datetime import datetime

import pytest

from maintops_core.incidents import ServiceError
from maintops_core.trips import choose_mode, expected_arrival


@pytest.mark.parametrize("requested", ["drive", "transit"])
def test_handyman_with_a_car_chooses(requested):
    assert choose_mode(True, requested) == requested


def test_handyman_with_a_car_must_choose():
    with pytest.raises(ServiceError, match="by car or by public transport"):
        choose_mode(True, None)


@pytest.mark.parametrize("requested", [None, "", "transit"])
def test_without_a_car_it_is_public_transport(requested):
    assert choose_mode(False, requested) == "transit"


def test_without_a_car_driving_is_refused():
    with pytest.raises(ServiceError, match="don't have a car"):
        choose_mode(False, "drive")


def test_expected_arrival_adds_the_travel_time():
    assert expected_arrival(datetime(2026, 10, 2, 12, 0), 23.6) == datetime(2026, 10, 2, 12, 23, 36)
