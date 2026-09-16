from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_fixture_mode_renders_all_sections_without_errors() -> None:
    dashboard = Path(__file__).parents[2] / "dashboard" / "app.py"
    app = AppTest.from_file(str(dashboard)).run(timeout=30)

    assert not app.exception
    assert [tab.label for tab in app.tabs] == [
        "Overview",
        "Futures Curve",
        "Kalshi",
        "Probability Model",
        "Trade Analysis",
        "Risk",
        "Historical",
    ]
