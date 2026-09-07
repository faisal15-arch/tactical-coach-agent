"""Pitch Tool — mocked. Later this could come from a scraped pitch report
or manual coach input via the dashboard."""


def get_pitch_condition(venue: str = "mock-venue") -> dict:
    return {
        "pitch": "Green",
        "bounce": "medium-high",
        "spin_assistance": "low",
    }
