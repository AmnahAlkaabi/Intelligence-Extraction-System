"""Deterministic ID-document format validation.

Runs directly on a file's raw extracted text -- never on an LLM's
PIIFinding.value_redacted, which by design (see prompts.PII_SYSTEM) never
carries enough of the original value to check a format or checksum
against; that redaction is deliberate and this module doesn't try to
reverse it. This is a second, independent, non-LLM detector whose results
get merged into result.pii_findings alongside run_pii's (see
domain_managers.py) -- it complements the LLM rather than replacing or
"scoring" it: the LLM catches PII by context and language across any
document, this catches a handful of internationally standardized
identifier formats by pattern (and, where a spec defines one, a real
checksum), independent of what the LLM did or didn't notice.

Two tiers, deliberately kept distinct via PIIFinding.detection_method:

  "rules_checksum" -- the format has ONE public specification with a real
  checksum, identical for every country/issuer that uses it, so a match
  is a strong positive signal, not a guess:
    - Passport MRZ (ICAO Doc 9303, TD3 two-line format printed on the
      photo page of every modern passport worldwide) -- weighted mod-10
      check digits over the passport number, DOB, and expiry date.
    - IBAN (ISO 13616) -- mod-97 checksum.
    - Payment card numbers (ISO/IEC 7812) -- Luhn checksum.

  "rules_shape" -- a small registry of well-documented NATIONAL formats
  matched by shape only (length + character classes, plus any publicly
  documented structural exclusions, e.g. invalid SSN ranges). These do
  NOT have a checksum verified here -- unlike the three formats above,
  there isn't one universal public spec for these to lean on, and getting
  a checksum algorithm subtly wrong would be worse than not claiming one
  (false confidence). Extend _NATIONAL_ID_PATTERNS as more formats are
  confirmed against an authoritative source, not guessed.
"""
import re
from dataclasses import dataclass

from app.models.schemas import PIIFinding


def _redact(value: str) -> str:
    """Same masking convention prompts.PII_SYSTEM already asks the LLM to
    use -- first/last character visible, everything else replaced. This
    module never returns or stores the raw matched value beyond this."""
    v = value.strip()
    if len(v) <= 2:
        return "*" * len(v)
    return v[0] + "*" * (len(v) - 2) + v[-1]


# ---------------------------------------------------------------------
# Passport MRZ (ICAO Doc 9303, TD3 format)
# ---------------------------------------------------------------------
_MRZ_WEIGHTS = (7, 3, 1)
_MRZ_LINE1_RE = re.compile(r"^P[A-Z<][A-Z<]{3}[A-Z<]{39}$")
_MRZ_LINE2_RE = re.compile(r"^[A-Z0-9<]{44}$")


def _mrz_char_value(ch: str) -> int:
    if ch == "<":
        return 0
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") + 10


def _mrz_check_digit(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        total += _mrz_char_value(ch) * _MRZ_WEIGHTS[i % 3]
    return total % 10


def _find_mrz_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    lines = [ln.strip().replace(" ", "") for ln in text.splitlines()]
    for i in range(len(lines) - 1):
        l1, l2 = lines[i], lines[i + 1]
        if len(l1) != 44 or len(l2) != 44:
            continue
        if not _MRZ_LINE1_RE.match(l1) or not _MRZ_LINE2_RE.match(l2):
            continue
        passport_no, check1 = l2[0:9], l2[9]
        dob, check2 = l2[13:19], l2[19]
        expiry, check3 = l2[21:27], l2[27]
        if (
            str(_mrz_check_digit(passport_no)) != check1
            or str(_mrz_check_digit(dob)) != check2
            or str(_mrz_check_digit(expiry)) != check3
        ):
            continue  # not a real MRZ block (or OCR noise) -- don't report a false positive
        findings.append(PIIFinding(
            category="PASSPORT",
            value_redacted=_redact(passport_no.replace("<", "")),
            severity="critical",
            source_file=source_file,
            location="Machine Readable Zone (MRZ)",
            detection_method="rules_checksum",
        ))
    return findings


# ---------------------------------------------------------------------
# IBAN (ISO 13616)
# ---------------------------------------------------------------------
# Grouped in blocks of up to 4 (how IBANs are conventionally printed,
# spaced or not) rather than a loose "[A-Z0-9 ]{11,34}" -- that looser
# version greedily ate through the space after an unspaced IBAN into the
# next word of surrounding prose (e.g. "...013000 by Friday" got captured
# as one candidate, blowing past the 34-char length cap and silently
# failing to match at all). Each repeated block is exactly 4 chars so it
# can't accidentally absorb an adjacent short English word.
_IBAN_CANDIDATE_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}[ ]?[A-Z0-9]{0,4})\b")


def _iban_checksum_ok(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    try:
        digits = "".join(str(int(ch, 36)) for ch in rearranged)
    except ValueError:
        return False
    return int(digits) % 97 == 1


def _find_iban_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _IBAN_CANDIDATE_RE.finditer(text.upper()):
        candidate = m.group(1).replace(" ", "")
        if not (15 <= len(candidate) <= 34) or not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]+$", candidate):
            continue
        if _iban_checksum_ok(candidate):
            findings.append(PIIFinding(
                category="IBAN",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="IBAN pattern (checksum-verified)",
                detection_method="rules_checksum",
            ))
    return findings


# ---------------------------------------------------------------------
# Payment card numbers (ISO/IEC 7812, Luhn checksum)
# ---------------------------------------------------------------------
_CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_valid(number: str) -> bool:
    digits = [int(d) for d in number]
    parity = len(digits) % 2
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _find_card_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _CARD_CANDIDATE_RE.finditer(text):
        candidate = re.sub(r"[ -]", "", m.group(0))
        if not (13 <= len(candidate) <= 19):
            continue
        if _luhn_valid(candidate):
            findings.append(PIIFinding(
                category="CREDIT_CARD",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="card number pattern (Luhn-verified)",
                detection_method="rules_checksum",
            ))
    return findings


# ---------------------------------------------------------------------
# National ID formats -- shape only, no checksum claimed (see module
# docstring). Each entry: (PII category, human label, compiled regex).
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class _NationalIdPattern:
    category: str
    label: str
    regex: re.Pattern


_NATIONAL_ID_PATTERNS: tuple[_NationalIdPattern, ...] = (
    # US SSN: XXX-XX-XXXX. Area 000/666/900-999, group 00, and serial 0000
    # are documented-invalid ranges the Social Security Administration
    # never issues -- excluding them cuts an easy class of false positives
    # (phone-number-shaped or arbitrary digit strings) without claiming a
    # checksum that doesn't exist for SSNs.
    _NationalIdPattern(
        "NATIONAL_ID", "US Social Security Number (structural match)",
        re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    ),
    # UAE Emirates ID: fixed 784 country prefix + 15 digits total
    # (784-YYYY-NNNNNNN-C), commonly printed with or without hyphens.
    _NationalIdPattern(
        "EMIRATES_ID", "UAE Emirates ID (structural match)",
        re.compile(r"\b784-?\d{4}-?\d{7}-?\d\b"),
    ),
)


def _find_national_id_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for pat in _NATIONAL_ID_PATTERNS:
        for m in pat.regex.finditer(text):
            findings.append(PIIFinding(
                category=pat.category,
                value_redacted=_redact(m.group(0)),
                severity="high",
                source_file=source_file,
                location=pat.label,
                detection_method="rules_shape",
            ))
    return findings


def detect_standard_ids(text: str, source_file: str) -> list[PIIFinding]:
    """Entry point: runs every pattern family against this file's raw
    text and returns whatever it independently found. Pure, synchronous,
    fast (plain regex over ordinary document-length text) -- safe to call
    directly from async code without asyncio.to_thread."""
    if not text.strip():
        return []
    findings: list[PIIFinding] = []
    findings.extend(_find_mrz_findings(text, source_file))
    findings.extend(_find_iban_findings(text, source_file))
    findings.extend(_find_card_findings(text, source_file))
    findings.extend(_find_national_id_findings(text, source_file))
    return findings
