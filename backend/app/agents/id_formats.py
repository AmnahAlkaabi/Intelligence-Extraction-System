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

  "rules_checksum" -- the format has a public specification with a real
  checksum. Confidence basis noted per format since some are cross-verified
  against multiple independent sources and some against one:
    - Passport MRZ (ICAO Doc 9303, TD3 two-line format printed on the
      photo page of every modern passport worldwide) -- weighted mod-10
      check digits over the passport number, DOB, and expiry date.
    - IBAN (ISO 13616) -- mod-97 checksum, one algorithm for every
      IBAN-using country.
    - Payment card numbers (ISO/IEC 7812) -- Luhn checksum.
    - India Aadhaar (UIDAI) -- Verhoeff checksum. Table values (the D, P,
      and inverse tables the algorithm is built from) cross-verified
      against two independent real implementations (Apache Commons
      Validator's VerhoeffCheckDigit.java and a separate GitHub Aadhaar
      generator) that agree digit-for-digit.
    - Brazil CPF -- two mod-11 check digits. Verified against two
      commonly-cited known-valid CPF test numbers, not just the algorithm
      description.
    - Canada SIN and South Africa ID -- both use the plain Luhn algorithm
      (same one as payment cards, just applied to 9 or 13 digits), per
      multiple independent sources with no documented deviation.
    - China Resident ID (GB 11643, ISO 7064 MOD 11-2) -- weighted checksum
      with a documented weight table and remainder-to-check-character
      mapping. Single detailed source for the exact table values -- flagged
      as the least independently cross-verified of this checksum tier, so
      treat a match here as a good signal but with a bit more caution than
      the others until cross-checked against a second source.

  "rules_shape" -- a small registry of well-documented NATIONAL formats
  matched by shape only (length + character classes, plus any publicly
  documented structural exclusions, e.g. invalid SSN ranges). These do
  NOT have a checksum verified here -- there isn't one universal public
  spec for these to lean on, and getting a checksum algorithm subtly
  wrong would be worse than not claiming one (false confidence). Extend
  _NATIONAL_ID_PATTERNS as more formats are confirmed against an
  authoritative source, not guessed.
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
# India Aadhaar (UIDAI) -- Verhoeff checksum. D/P/inv tables cross-verified
# against two independent real implementations (Apache Commons Validator's
# VerhoeffCheckDigit.java and a separate GitHub Aadhaar generator) that
# agree digit-for-digit -- see module docstring.
# ---------------------------------------------------------------------
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
# _VERHOEFF_INV unused by validation (only needed to *generate* a new
# check digit, which this module never does) -- kept out to avoid dead code.

_AADHAAR_CANDIDATE_RE = re.compile(r"\b(\d{4}[ ]?\d{4}[ ]?\d{4})\b")


def _verhoeff_valid(code: str) -> bool:
    """checksum accumulates right-to-left; valid iff it lands back on 0.
    Matches Apache Commons Validator's calculateChecksum(code, includesCheckDigit=True)."""
    checksum = 0
    for i, ch in enumerate(reversed(code)):
        if not ch.isdigit():
            return False
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[i % 8][int(ch)]]
    return checksum == 0


def _find_aadhaar_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _AADHAAR_CANDIDATE_RE.finditer(text):
        candidate = m.group(1).replace(" ", "")
        if len(candidate) != 12 or candidate[0] in "01":  # UIDAI: never starts with 0 or 1
            continue
        if _verhoeff_valid(candidate):
            findings.append(PIIFinding(
                category="NATIONAL_ID",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="India Aadhaar (Verhoeff-verified)",
                detection_method="rules_checksum",
            ))
    return findings


# ---------------------------------------------------------------------
# Brazil CPF -- two mod-11 check digits over digits 1-9 and 1-10.
# Verified against two commonly-cited known-valid CPF test numbers
# (529.982.247-25 and 111.444.777-35), not just the algorithm description.
# ---------------------------------------------------------------------
_CPF_CANDIDATE_RE = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b")


def _cpf_check_digit(digits: str, weights: tuple[int, ...]) -> int:
    total = sum(int(d) * w for d, w in zip(digits, weights))
    rem = total % 11
    return 0 if rem < 2 else 11 - rem


def _cpf_valid(cpf: str) -> bool:
    if len(cpf) != 11 or len(set(cpf)) == 1:  # reject all-same-digit repdigits (mathematically pass but invalid)
        return False
    check1 = _cpf_check_digit(cpf[0:9], (10, 9, 8, 7, 6, 5, 4, 3, 2))
    check2 = _cpf_check_digit(cpf[0:9] + str(check1), (11, 10, 9, 8, 7, 6, 5, 4, 3, 2))
    return cpf[9] == str(check1) and cpf[10] == str(check2)


def _find_cpf_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _CPF_CANDIDATE_RE.finditer(text):
        candidate = re.sub(r"[.\-]", "", m.group(1))
        if _cpf_valid(candidate):
            findings.append(PIIFinding(
                category="NATIONAL_ID",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="Brazil CPF (mod-11 verified)",
                detection_method="rules_checksum",
            ))
    return findings


# ---------------------------------------------------------------------
# Canada SIN and South Africa ID -- both the plain Luhn algorithm (same
# one as payment cards, above), just applied to a different digit count.
# South Africa ID additionally checks that its first 6 digits form a
# plausible YYMMDD date (it encodes the holder's DOB) -- narrows false
# positives against arbitrary 13-digit Luhn-valid numbers, which would
# otherwise also satisfy the generic card-number check above.
# ---------------------------------------------------------------------
_SIN_CANDIDATE_RE = re.compile(r"\b(\d{3}[ -]?\d{3}[ -]?\d{3})\b")
_SA_ID_CANDIDATE_RE = re.compile(r"\b(\d{13})\b")


def _find_sin_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _SIN_CANDIDATE_RE.finditer(text):
        candidate = re.sub(r"[ -]", "", m.group(1))
        if len(candidate) == 9 and _luhn_valid(candidate):
            findings.append(PIIFinding(
                category="NATIONAL_ID",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="Canada SIN (Luhn-verified)",
                detection_method="rules_checksum",
            ))
    return findings


def _is_plausible_yymmdd(s: str) -> bool:
    mm, dd = int(s[2:4]), int(s[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31


def _find_south_africa_id_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _SA_ID_CANDIDATE_RE.finditer(text):
        candidate = m.group(1)
        if not _is_plausible_yymmdd(candidate) or candidate[10] not in "01":  # citizenship digit
            continue
        if _luhn_valid(candidate):
            findings.append(PIIFinding(
                category="NATIONAL_ID",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="South Africa ID (Luhn-verified)",
                detection_method="rules_checksum",
            ))
    return findings


# ---------------------------------------------------------------------
# China Resident ID (GB 11643, ISO 7064 MOD 11-2). Weight table and
# remainder-to-check-character mapping from a single detailed source --
# the least independently cross-verified format in this checksum tier
# (see module docstring); still a real published national standard, not
# a guess, but worth a second-source check before leaning on it heavily.
# ---------------------------------------------------------------------
_CHINA_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHINA_ID_CHECK_CHARS = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
_CHINA_ID_CANDIDATE_RE = re.compile(r"\b(\d{17}[\dXx])\b")


def _china_id_valid(code: str) -> bool:
    total = sum(int(d) * w for d, w in zip(code[:17], _CHINA_ID_WEIGHTS))
    return _CHINA_ID_CHECK_CHARS[total % 11] == code[17].upper()


def _find_china_id_findings(text: str, source_file: str) -> list[PIIFinding]:
    findings = []
    for m in _CHINA_ID_CANDIDATE_RE.finditer(text):
        candidate = m.group(1)
        if _china_id_valid(candidate):
            findings.append(PIIFinding(
                category="NATIONAL_ID",
                value_redacted=_redact(candidate),
                severity="critical",
                source_file=source_file,
                location="China Resident ID (MOD 11-2 verified)",
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
    findings.extend(_find_aadhaar_findings(text, source_file))
    findings.extend(_find_cpf_findings(text, source_file))
    findings.extend(_find_sin_findings(text, source_file))
    findings.extend(_find_south_africa_id_findings(text, source_file))
    findings.extend(_find_china_id_findings(text, source_file))
    findings.extend(_find_national_id_findings(text, source_file))
    return findings
