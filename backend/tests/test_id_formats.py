"""Tests for agents/id_formats.py -- previously zero coverage.

Focused on the credit-card false-positive fix: the generic Luhn-only card
detector flagged any dash-formatted 13-19 digit ID (e.g. a UAE Emirates
ID, which the module's own EMIRATES_ID pattern already recognizes
correctly) as a CRITICAL-severity credit card purely by chance (~10% of
arbitrary digit runs pass Luhn). Fixed by additionally requiring the
candidate to fall within a real card network's documented IIN range.
"""
from app.agents.id_formats import detect_standard_ids


def _categories(text: str) -> list[str]:
    return [f.category for f in detect_standard_ids(text, "test.json")]


def test_emirates_id_is_not_misdetected_as_a_credit_card():
    """Regression test: this exact value was flagged as CREDIT_CARD
    (critical) before the IIN-range fix, purely because it happened to
    pass Luhn -- "784" isn't a real card network prefix."""
    text = 'national_id: "784-1985-9876543-2"'
    cats = _categories(text)
    assert "EMIRATES_ID" in cats
    assert "CREDIT_CARD" not in cats


def test_real_card_numbers_are_still_detected():
    real_cards = {
        "4532015112830366": "Visa",
        "4222222222222": "Visa (13-digit)",
        "5425233430109903": "Mastercard",
        "374245455400126": "American Express",
        "6011111111111117": "Discover",
        "30569309025904": "Diners Club",
        "3530111333300000": "JCB",
        "6212345678901232": "UnionPay",
    }
    for number, label in real_cards.items():
        cats = _categories(f"Card number: {number}")
        assert "CREDIT_CARD" in cats, f"{label} ({number}) should be detected"


def test_arbitrary_luhn_valid_non_card_number_is_not_flagged():
    """A 15-digit Luhn-valid string with no real network prefix (doesn't
    start with 3/4/5/6xx in any of the checked ranges) should not be
    reported as a credit card."""
    # 700000000000003 -> Luhn-valid, starts with "70" (not any real IIN range)
    cats = _categories("reference number 700000000000003")
    assert "CREDIT_CARD" not in cats


def test_brazil_cpf_known_valid_numbers_still_detected():
    for cpf in ("529.982.247-25", "111.444.777-35"):
        cats = _categories(f"CPF: {cpf}")
        assert "NATIONAL_ID" in cats


def test_empty_text_returns_no_findings():
    assert detect_standard_ids("", "test.json") == []
    assert detect_standard_ids("   ", "test.json") == []
