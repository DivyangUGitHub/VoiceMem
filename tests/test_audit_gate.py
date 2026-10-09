import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_gate import check, load_exceptions  # noqa: E402

TODAY = date(2026, 10, 8)
REPORT = {"dependencies": [
    {"name": "transformers", "version": "4.52.3", "vulns": [{"id": "A"}, {"id": "B"}]},
    {"name": "requests", "version": "2.0", "vulns": []},
]}


def test_clean_report_passes():
    assert check({"dependencies": [{"name": "x", "version": "1", "vulns": []}]}, {}, TODAY) == ([], [])


def test_unknown_vulnerability_fails():
    failures, accepted = check(REPORT, {}, TODAY)
    assert len(failures) == 1 and "transformers==4.52.3" in failures[0] and not accepted


def test_exception_is_accepted_until_its_date():
    failures, accepted = check(REPORT, {"transformers": date(2026, 10, 8)}, TODAY)
    assert not failures and "accepted until 2026-10-08" in accepted[0]


def test_expired_exception_fails():
    failures, _ = check(REPORT, {"transformers": date(2026, 10, 7)}, TODAY)
    assert "expired" in failures[0]


def test_shipped_exceptions_file_parses_and_names_transformers():
    assert "transformers" in load_exceptions()
