"""Parse a pasted ICE Chat broker string into trade fields.

Deliberately a plain deterministic parser rather than a model: the same
string must always produce the same trade, the rules have to be testable
against real broker text, and a wrong guess here writes a bad row into a
compliance database.

Written against these real strings:

    APS SELLS/MAG BUYS 100 MWS HE18-HE21  PV FIXED $73 flow 9/15  wspp sched c
    APS SELLS/MAG BUYS 12 MWS PV HL @ index +.50  flow 9/15 wspp sched c
    BPA sells 100MW LL ACS at JD for midc+3
    el paso buys 100mw H18-21 springer  $83
    BPA buys 50mw nws LL at midc for midc-3
    APS sells 50MW ncs he17-22 at PV for $65 flow date 09/17
    APS sells 50MW ncs he17-22 Mon only at PV for $65
    ABEX sells 2mw atc at Glacier for $28 sched B
    NWMT sells 50mw HE18 at crossover for $45
    MAG buys from Conoco 75mw of Non-caiso power for Q3 HL 2027 at PV index + 9.5
    NEVP buys 25mw he17-22 ncs Jul-Aug 28 at Navajo for $216
    NEVP buys 25mw he17-22 ncs Jul 28 at Navajo for $230
    APS sells 50MW ncs he17-22 at PV for $65 via BGC
    epe sells 150mw he9-12 and 100mw he13-14 at FC $4

Each extractor scans the whole string and blanks out the span it claims, so
field order never matters — only a few extractors run in a fixed order to
resolve genuine ambiguity (`at midc` the POR/POD vs `for midc-3` the node).

This module has no Streamlit or database imports: the caller passes in the
vocabulary, and the parser only produces values. Nothing here writes
anything.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import get_close_matches
import re

# --- Desk shorthand ------------------------------------------------------
# Extend as new shorthand turns up. Keys are matched case-insensitively.
COUNTERPARTY_ALIASES = {
    "APS": "AZPS",
    "SCL": "SCLM",
    "BPA": "BPAT",
    "PGE": "PGEM",
    "SCE": "SCET",
    "NWMT": "NWDS",
    "CONOCO": "CONC",   
    "TEP": "TEPM",
    "TEPC": "TEPM"
}

#: The desk's two single-day products, written "1x16" and "1x8" (spaces
#: around the x are tolerated). Sixteen on-peak hours and the eight
#: off-peak ones, on one named day whatever kind of day it is — the hours
#: themselves live in domain.shapes.FIXED_SHAPE_HOURS, which is where they
#: are turned into a schedule. This module names only what to look for.
_SINGLE_DAY_SHAPE_PATTERN = r"\b1\s*x\s*(16|8)\b"

LOCATION_ALIASES = {
    "PV": "PALOVERDE500",
    "PALOVERDE": "PALOVERDE500",
    "PV500": "PALOVERDE500",
    "JD": "JohnDay",
    "JOHNDAY": "JohnDay",
    "SPRINGER": "SPRINGER345",
    "MEAD": "MEAD230",
    "MID-C": "MIDC",
    "MIDC": "MIDC",
    "FOURCORNERS": "FOURCORNE345",
    "4C": "FOURCORNE345",
    "FC": "FOURCORNE345",
    "FC345": "FOURCORNE345",
    "MALIN": "MALIN500",
    # The California-Oregon Border, which the desk trades as the same
    # point as Malin and writes either way round in a broker string.
    "COB": "MALIN500",
    "NAVAJO": "NAVAJO500",
    "WESTWING": "WESTWING500",
    "MATL": "MATL.NWMT",
    "GLACIER": "GLWND1"
}

INDEX_ALIASES = {
    "PV": "PALOVERDE",
    "MID-C": "MIDC",
    "MIDC": "MIDC",
    "MEAD": "MEAD230",
}

# For the "@ index +.50" form, which never names the node — it means "the
# index at this location".
LOCATION_TO_INDEX = {
    "PALOVERDE500": "PALOVERDE",
    "MEAD230": "MEAD230",
    "MIDC": "MIDC",
    "MDWP": "MONA",
}

# ACS = Asset Controlling Supplier. Which source it means depends on who is
# selling it, so this maps counterparty -> the SPECIFIED_SOURCES entry.
ACS_SOURCES = {
    "BPAT": "Bonneville Power Administration",
    "TPWP": "Tacoma Power - ACS",
    "SCLM": "Seattle City Light - ACS",
}

# SS = Specified Source. Unlike ACS it doesn't name one, it only says there
# is one — so which it means is again a property of the counterparty. A
# counterparty with no entry here leaves the field empty with a warning
# rather than failing the paste: "SS" is a flag, not a claim about which
# plant, so there's nothing to guess wrong, and the trader sets it in the
# form. (ACS *is* such a claim, which is why an unmapped one is an error.)
SS_SOURCES = {
    "SCLM": "Boundary Dam Hydro",
}

# How the trade was communicated, matched case-insensitively against the
# desk's shorthand for each Communication option. "ice chat" (the two-word
# phrase) is listed ahead of the bare "ice"/"chat" tokens it's made of, and
# sorted longest-first below, so it wins over either alone.
COMMUNICATION_ALIASES = {
    "itap": "ITAP",
    "ice chat": "ICE Chat",
    "ice": "ICE",
    "bgc": "Broker - BGC",
    "equus": "Broker - Equus",
    "tullett": "Broker - Tullett",
    "emilio": "Phone - Emilio",
    "thomas": "Phone - Thomas",
    "charles": "Phone - Charles",
    "byron": "Phone - Byron",
    "chat": "ICE Chat",
    "email": "Email"
}
_COMMUNICATION_PATTERN = (
    r"\b(?:via\s+)?(" + "|".join(sorted(COMMUNICATION_ALIASES, key=len, reverse=True)) + r")\b"
)

# Our own side, as it appears in broker strings and in BilateralMarket.
SELF_NAME = "MAG"

# A bare weekday ("Mon", "Mon only") names a single flow date directly,
# instead of the usual numeric flow date — the nearest occurrence of that
# weekday on or after the trade date. Needed for e.g. a trade struck on a
# Sunday that only covers the Monday, not both days. Monday=0 ... Sunday=6,
# matching date.weekday(). Sorted longest-first so "monday" isn't cut short
# by "mon" winning the alternation first.
WEEKDAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tues": 1, "tue": 1,
    "wednesday": 2, "weds": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_WEEKDAY_PATTERN = (
    r"\b(" + "|".join(sorted(WEEKDAY_NAMES, key=len, reverse=True)) + r")\b"
    r"(?:\s+only\b)?"
)

# A "Jul-Aug 28" month range names a flow-date range directly: the first day
# of the first month through the last day of the second, both in the given
# (two- or four-digit) year. Sorted longest-first so "jul" isn't cut short by
# a shorter alternative winning the match first.
MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_PATTERN = "(" + "|".join(sorted(MONTH_NAMES, key=len, reverse=True)) + ")"
_MONTH_RANGE_PATTERN = (
    r"\b(?:for\s+)?" + _MONTH_PATTERN + r"\s*-\s*" + _MONTH_PATTERN + r"\s+(\d{2,4})\b"
)
# A single month ("Jul 28") names that whole month as the flow-date range —
# tried only when the range form above doesn't match, so "Jul-Aug 28" is
# never read as bare "Jul" plus a stray "-Aug 28".
_SINGLE_MONTH_PATTERN = r"\b(?:for\s+)?" + _MONTH_PATTERN + r"\s+(\d{2,4})\b"

FUZZY_CUTOFF = 0.8

# Fields a string must supply; anything else falls back to a default.
REQUIRED_FIELDS = ("counterparty", "direction", "mw", "shape", "location", "price")

#: A quantity: "100MW", "12 MWS", "2.5mw".
_MW_PATTERN = r"(\d+(?:\.\d+)?)\s*mws?\b"

#: Every shape a quantity can carry, as one alternation. The hour range is
#: listed before the single hour so "he9-12" isn't read as "he9" with "-12"
#: left over.
_SHAPE_TOKEN = (
    r"1\s*x\s*(?:16|8)"
    r"|h(?:e)?\s*\d{1,2}\s*-\s*(?:h(?:e)?\s*)?\d{1,2}"
    r"|h(?:e)?\s*\d{1,2}"
    r"|hl|ll|atc"
)

#: A shape sitting immediately after its MW — "150mw he9-12". Anchored, and
#: only used when the string names more than one quantity; see
#: _extract_segments.
_SHAPE_AFTER_MW = re.compile(r"\s*(?:of\s+)?(" + _SHAPE_TOKEN + r")\b", re.I)

#: What joins one segment to the next. Matched ending where the next
#: quantity starts, so it is consumed with the segment rather than left to
#: turn up in the "Ignored:" warning.
_SEGMENT_JOIN = re.compile(r"\s*(?:and|&|\+|,|/)\s*$", re.I)


@dataclass
class ParsedField:
    value: object
    source_text: str          # the token this came from
    confidence: str = "exact"  # "exact" | "alias" | "fuzzy" | "derived"


@dataclass
class ParsedTrade:
    fields: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)    # non-empty => fill nothing
    warnings: list = field(default_factory=list)  # fuzzy hits, leftover text

    def get(self, name, default=None):
        found = self.fields.get(name)
        return found.value if found else default

    @property
    def ok(self):
        return not self.errors


class _Scanner:
    """Holds the string with claimed spans blanked out, so each extractor
    only ever sees text nothing else has taken."""

    def __init__(self, text):
        self.original = text
        self.remaining = text

    def _consume(self, start, end):
        self.remaining = (
            self.remaining[:start] + " " * (end - start) + self.remaining[end:]
        )

    def take(self, pattern):
        m = re.search(pattern, self.remaining, re.I)
        if m:
            self._consume(*m.span())
        return m

    def take_at(self, match):
        self._consume(*match.span())

    def find_all(self, pattern):
        return list(re.finditer(pattern, self.remaining, re.I))

    def leftover_tokens(self):
        return [t for t in re.split(r"[\s,;/]+", self.remaining) if t.strip(".$-")]


def _norm(text):
    return re.sub(r"[^A-Z0-9.]", "", (text or "").upper())


def resolve_token(token, canonical, aliases=None, full_names=None):
    """Resolve broker shorthand to a canonical value.

    Tries, in order: alias table, exact match, full-name substring, then a
    fuzzy match. Returns (value, confidence) or (None, None). Fuzzy hits are
    labelled so the caller can flag them rather than let them pass as exact.
    """
    key = _norm(token)
    if not key:
        return None, None

    exact = {_norm(c): c for c in canonical if c}

    if aliases:
        by_norm = {_norm(k): v for k, v in aliases.items()}
        if key in by_norm:
            target = by_norm[key]
            # Prefer the option list's own spelling — the alias table and the
            # DB disagree on case in places ('JohnDay' vs 'JOHNDAY'), and the
            # value has to be one the dropdown actually offers.
            return exact.get(_norm(target), target), "alias"

    if key in exact:
        return exact[key], "exact"

    # Full names, e.g. "el paso" -> "El Paso Electric Company" -> EPE.
    if full_names:
        words = re.sub(r"[^A-Z0-9 ]", "", (token or "").upper()).strip()
        if words:
            for code, full in full_names.items():
                if words in re.sub(r"[^A-Z0-9 ]", "", (full or "").upper()):
                    return code, "alias"

    close = get_close_matches(key, list(exact), n=1, cutoff=FUZZY_CUTOFF)
    if close:
        return exact[close[0]], "fuzzy"
    return None, None


def _infer_year(month, day, today):
    """Pick the year that puts this month/day nearest to today, so a 12/31
    pasted in January doesn't land eleven months in the past."""
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - today).days))


def _premium(raw):
    """'+ .50' -> 0.5, '-3' -> -3.0."""
    return float(re.sub(r"\s+", "", raw))


def _last_day_of_month(year, month):
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _quarter_bounds(quarter, year):
    """First and last calendar day of a quarter, e.g. (3, 2027) -> Jul 1 ..
    Sep 30, 2027."""
    start_month = 3 * (quarter - 1) + 1
    start = date(year, start_month, 1)
    end = _last_day_of_month(year, start_month + 2)
    return start, end


def _full_year(year_str):
    """'28' -> 2028, '2028' -> 2028. Desk shorthand only ever means the
    2000s, so a two-digit year doesn't need today-relative inference the
    way an MM/DD flow date does."""
    year_str = year_str.strip()
    return int(year_str) if len(year_str) > 2 else 2000 + int(year_str)


def _month_range_bounds(month1, month2, year):
    """First day of `month1` through the last day of `month2`, e.g.
    (7, 8, 2028) -> Jul 1 .. Aug 31, 2028. `month2` rolling before `month1`
    (e.g. Nov-Jan) rolls the end into the following year."""
    start = date(year, month1, 1)
    end_year = year if month2 >= month1 else year + 1
    end = _last_day_of_month(end_year, month2)
    return start, end


def _next_weekday_on_or_after(start, weekday_num):
    """The nearest date >= `start` that falls on `weekday_num` (Monday=0).
    Returns `start` itself when it already matches."""
    return start + timedelta(days=(weekday_num - start.weekday()) % 7)


_VERB_RE = r"\b(buys?|sells?|sold|bought)\b"


#: How many words before a buy/sell verb can make up a party name
#: ("el paso" is two, "APS" one). Tried longest-first.
_MAX_PARTY_WORDS = 3


def _party_candidates(text, seg_start, verb_start):
    """The trailing 1..N words before a verb, longest first, each with the
    absolute offset where it begins.

    Bounded this way because the words before a verb are only *sometimes*
    the party name — in a reordered string the same run holds the MW and the
    flow date too, and swallowing those loses the fields they belong to.
    """
    segment = text[seg_start:verb_start]
    words = [(m.group(), seg_start + m.start()) for m in re.finditer(r"\S+", segment)]
    for size in range(min(_MAX_PARTY_WORDS, len(words)), 0, -1):
        chosen = words[-size:]
        yield " ".join(w for w, _ in chosen), chosen[0][1]


def _party_words_after(text, start):
    """Mirror of `_party_candidates`, but scanning forward from `start` —
    for 'MAG buys from Conoco 75mw...', where the counterparty follows
    'from' rather than having its own verb. Longest first, each with the
    absolute (start, end) span it occupies."""
    words = [
        (m.group(), start + m.start(), start + m.end())
        for m in re.finditer(r"\S+", text[start:])
    ]
    for size in range(min(_MAX_PARTY_WORDS, len(words)), 0, -1):
        chosen = words[:size]
        yield " ".join(w for w, _, _ in chosen), chosen[0][1], chosen[-1][2]


def _extract_parties(scanner, counterparties, full_names, result):
    """Counterparty and direction, from MAG's point of view.

    'MAG BUYS' is used directly; with only the counterparty named, its verb
    inverts ('BPA sells' -> we buy).
    """
    text = scanner.remaining
    matches = scanner.find_all(_VERB_RE)
    if not matches:
        result.errors.append(
            "No buy/sell wording found — expected something like "
            "'APS SELLS/MAG BUYS' or 'BPA sells'."
        )
        return

    parties = []  # (name, resolved, confidence, verb, name_start, verb_end)
    prev_end = 0
    for m in matches:
        delimiter = max(
            (text.rfind(d, prev_end, m.start()) for d in "/,;"), default=-1
        )
        seg_start = delimiter + 1 if delimiter >= 0 else prev_end
        verb = m.group(1).lower()

        chosen = None
        for name, start in _party_candidates(text, seg_start, m.start()):
            if _norm(name) == SELF_NAME:
                chosen = (name, SELF_NAME, "exact", verb, start, m.end())
                break
            value, confidence = resolve_token(
                name, counterparties, COUNTERPARTY_ALIASES, full_names
            )
            if value is not None:
                chosen = (name, value, confidence, verb, start, m.end())
                break
        if chosen is None:
            # Keep the single closest word for the error message.
            fallback = next(
                (n for n, _ in _party_candidates(text, seg_start, m.start())), ""
            )
            chosen = (fallback, None, None, verb, m.start(), m.end())
        parties.append(chosen)
        prev_end = m.end()

    us = [p for p in parties if p[1] == SELF_NAME]
    them = [p for p in parties if p[1] != SELF_NAME]

    if us:
        verb = us[0][3]
        direction = "Buy" if verb.startswith("b") else "Sell"
        source = f"{us[0][0]} {verb}"
    elif len(them) == 1:
        # Only the counterparty is named, so their verb is the mirror of ours.
        verb = them[0][3]
        direction = "Sell" if verb.startswith("b") else "Buy"
        source = f"{them[0][0]} {verb}"
    else:
        result.errors.append(
            "Could not tell which side MAG is on — name MAG explicitly, "
            "e.g. 'APS SELLS/MAG BUYS'."
        )
        return

    result.fields["direction"] = ParsedField(direction, source, "derived")

    if them:
        name, value, confidence, _, _, _ = them[0]
        if value is None:
            result.errors.append(f"Counterparty {name!r} not recognized.")
        else:
            result.fields["counterparty"] = ParsedField(value, name, confidence)
    else:
        # Only MAG's own verb was found ("MAG buys from Conoco ..."), so the
        # counterparty follows "from" instead of having its own verb.
        verb_end = us[0][5]
        fm = re.search(r"\bfrom\s+", text[verb_end:], re.I)
        found = None
        if fm:
            after = verb_end + fm.end()
            for name, start, end in _party_words_after(text, after):
                if _norm(name) == SELF_NAME:
                    continue
                value, confidence = resolve_token(
                    name, counterparties, COUNTERPARTY_ALIASES, full_names
                )
                if value is not None:
                    found = (name, value, confidence, start, end)
                    break
        if found:
            name, value, confidence, start, end = found
            result.fields["counterparty"] = ParsedField(value, name, confidence)
            scanner._consume(verb_end + fm.start(), end)
        else:
            result.errors.append("No counterparty found in the string.")

    # Blank each party name together with its verb, so neither can later be
    # mistaken for a location or a specified source.
    for _, _, _, _, name_start, verb_end in parties:
        scanner._consume(name_start, verb_end)


def _extract_price_and_index(scanner, indexes, result):
    """Four forms: 'FIXED $73', a bare '$83', 'for midc+3', and '@ index
    +.50' (which means the index at this location, resolved later)."""
    m = scanner.take(r"\bfor\s+([a-z0-9.]+)\s*([+-]\s*\d*\.?\d+)?")
    if m:
        token, premium = m.group(1), m.group(2)
        value, confidence = resolve_token(token, indexes, INDEX_ALIASES)
        if value is None:
            result.errors.append(f"Pricing index {token!r} not recognized.")
        else:
            result.fields["index"] = ParsedField(value, token, confidence)
        result.fields["price"] = ParsedField(
            _premium(premium) if premium else 0.0, m.group(0).strip(), "exact"
        )
        return

    m = scanner.take(r"@\s*index\s*([+-]?\s*\d*\.?\d+)?")
    if m:
        # Node comes from the location; filled in by _derive_index_from_location.
        result.fields["index"] = ParsedField(None, "@ index", "derived")
        result.fields["price"] = ParsedField(
            _premium(m.group(1)) if m.group(1) else 0.0, m.group(0).strip(), "exact"
        )
        return

    # Same "index at this location" meaning as '@ index', just without the
    # '@' — e.g. 'at PV index + 9.5'.
    m = scanner.take(r"\bindex\s*([+-]?\s*\d*\.?\d+)?")
    if m:
        result.fields["index"] = ParsedField(None, "index", "derived")
        result.fields["price"] = ParsedField(
            _premium(m.group(1)) if m.group(1) else 0.0, m.group(0).strip(), "exact"
        )
        return

    m = scanner.take(r"\bfixed\b\s*\$?\s*(-?\d+(?:\.\d+)?)")
    if m:
        result.fields["index"] = ParsedField(None, "fixed", "exact")
        result.fields["price"] = ParsedField(float(m.group(1)), m.group(0).strip())
        return

    m = scanner.take(r"\$\s*(-?\d+(?:\.\d+)?)")
    if m:
        result.fields["index"] = ParsedField(None, m.group(0).strip(), "exact")
        result.fields["price"] = ParsedField(float(m.group(1)), m.group(0).strip())


def _derive_index_from_location(result):
    """Resolve the '@ index' form now that the location is known."""
    index_field = result.fields.get("index")
    if not index_field or index_field.value is not None:
        return
    if index_field.source_text not in ("@ index", "index"):
        return
    location = result.get("location")
    if location is None:
        return  # a missing location is already reported
    node = LOCATION_TO_INDEX.get(location)
    if node is None:
        result.errors.append(
            f"'@ index' was used but {location} has no pricing node — "
            f"name the index instead, e.g. 'for midc+3'."
        )
        return
    result.fields["index"] = ParsedField(node, f"@ index ({location})", "derived")


def _shape_value(raw):
    """A matched shape token written the way the Shape box wants it:
    "HE9 - 12" -> "9-12", "1 x 16" -> "1x16", "hl" -> "HL"."""
    text = re.sub(r"\s+", "", raw).upper()
    m = re.fullmatch(r"1X(16|8)", text)
    if m:
        return f"1x{m.group(1)}"
    m = re.fullmatch(r"H(?:E)?(\d{1,2})(?:-(?:H(?:E)?)?(\d{1,2}))?", text)
    if m:
        start_he, end_he = int(m.group(1)), m.group(2)
        return f"{start_he}-{int(end_he)}" if end_he else str(start_he)
    return text


def _warn_single_day_shape(result, shape, source_text):
    """A 1x16 or 1x8 covers one day and the string didn't say which.

    Only silence needs flagging: a day named in the string is taken at its
    word. The form's own default window is the whole trading session, and
    these two products cover one day of it.
    """
    if shape in ("1x16", "1x8") and not result.get("start_date"):
        result.warnings.append(
            f"{source_text} covers a single day and the string doesn't say "
            "which. The flow dates are left at their default — set them "
            "before adding the trade."
        )


def _take_loose_shape(scanner, result):
    """The shape of a string naming one quantity, from anywhere in it.

    Loose because there is only one quantity it could belong to: the desk
    writes "12 MWS PV HL" and "75mw of Non-caiso power for Q3 HL 2027" and
    means the same thing by both. The single-day products are tried first,
    being the most specific thing in the sentence — they are not HL and LL,
    which ask the WECC calendar what kind of day it is where these name
    their hours outright, weekend days included (domain.shapes).
    """
    m = scanner.take(_SINGLE_DAY_SHAPE_PATTERN)
    if m:
        shape = f"1x{m.group(1)}"
        result.fields["shape"] = ParsedField(shape, m.group(0).strip())
        _warn_single_day_shape(result, shape, m.group(0).strip())
        return
    # The "-end" half is optional so a single hour ("HE18") is read as a
    # one-hour shape, same as typing "18" alone into the Shape box.
    m = scanner.take(r"\bh(?:e)?\s*(\d{1,2})(?:\s*-\s*(?:h(?:e)?\s*)?(\d{1,2}))?\b")
    if m:
        start_he, end_he = m.group(1), m.group(2)
        result.fields["shape"] = ParsedField(
            f"{int(start_he)}-{int(end_he)}" if end_he else str(int(start_he)),
            m.group(0).strip(),
        )
        return
    m = scanner.take(r"\b(hl|ll|atc)\b")
    if m:
        result.fields["shape"] = ParsedField(m.group(1).upper(), m.group(0).strip())
        return
    result.errors.append(
        "No shape found (expected HL, LL, ATC, 1x16, 1x8, or an hour range "
        "like HE18-HE21)."
    )


def _paired_segments(scanner, quantities, result):
    """Each quantity with the shape written right after it, or [] and an
    error saying which one has none.

    All-or-nothing, like every other refusal here: pairing by position
    instead would be a guess that silently moves a trade's energy to the
    wrong hours.
    """
    text = scanner.remaining
    segments, spans = [], []
    for index, m in enumerate(quantities):
        shape = _SHAPE_AFTER_MW.match(text, m.end())
        if shape is None:
            result.errors.append(
                f"{m.group(0).strip()} has no shape after it. A string naming "
                "more than one quantity needs a shape on each, like "
                "'150mw he9-12 and 100mw he13-14'."
            )
            return []
        value = _shape_value(shape.group(1))
        source_text = text[m.start():shape.end()].strip()
        _warn_single_day_shape(result, value, source_text)
        segments.append(
            {"mw": float(m.group(1)), "shape": value, "source_text": source_text}
        )
        start = m.start()
        if index:
            join = _SEGMENT_JOIN.search(text, 0, start)
            if join:
                start = join.start()
        spans.append((start, shape.end()))
    for start, end in spans:
        scanner._consume(start, end)
    return segments


def _extract_segments(scanner, result):
    """The MW quantities the string names, each with the shape it runs on.

    Usually one, and then the two are read apart (_take_loose_shape).
    **A string naming more than one is read strictly**: every quantity has
    to carry its own shape immediately after it, because with two of each
    loose in one sentence nothing says which goes with which.

    Each segment becomes its own schedule block on the form, and so its own
    `BilateralTrades` row — which is the format the back office's readers
    expect and the reason the trade is not folded into a single row here.
    It is still one trade: the board shows it as one square (see
    domain.matching.legs_from_db_rows) and the desk thinks of it as one.

    `segments` is always set when both an MW and a shape were found, so a
    caller never has to tell the one-segment case apart; `mw` and `shape`
    carry the first segment, which is what everything that predates splits
    reads.
    """
    quantities = scanner.find_all(_MW_PATTERN)
    if len(quantities) > 1:
        segments = _paired_segments(scanner, quantities, result)
        if not segments:
            return
        first = segments[0]
        result.fields["mw"] = ParsedField(first["mw"], first["source_text"])
        result.fields["shape"] = ParsedField(first["shape"], first["source_text"])
    else:
        if quantities:
            m = quantities[0]
            scanner.take_at(m)
            result.fields["mw"] = ParsedField(float(m.group(1)), m.group(0).strip())
        else:
            result.errors.append(
                "No MW quantity found (expected something like '100MW')."
            )
        _take_loose_shape(scanner, result)
        if not (result.get("mw") and result.get("shape")):
            return
        # Already on the result, each with its own span of the string
        # behind it; only the list view of them is new.
        segments = [
            {
                "mw": result.get("mw"),
                "shape": result.get("shape"),
                "source_text": (
                    f'{result.fields["mw"].source_text} '
                    f'{result.fields["shape"].source_text}'
                ),
            }
        ]

    result.fields["segments"] = ParsedField(
        segments, " and ".join(seg["source_text"] for seg in segments), "derived"
    )


def parse_trade_string(
    text,
    counterparties=(),
    locations=(),
    indexes=(),
    specified_sources=(),
    full_names=None,
    today=None,
):
    """Parse a broker string into trade fields.

    `today` anchors every relative date the string can carry — a bare
    weekday ("Mon only") and the year of an MM/DD flow date both resolve
    relative to it. Pass the trade's actual Trade Date here, not
    necessarily the real wall-clock date: a trader back-entering a past
    trade needs "Mon" to resolve against *that* date, not today's.

    Returns a ParsedTrade. When `errors` is non-empty the caller should fill
    nothing — a partly-filled form is worse than an obvious refusal.
    """
    today = today or date.today()
    result = ParsedTrade()
    if not (text or "").strip():
        result.errors.append("Nothing to parse.")
        return result

    scanner = _Scanner(text)

    _extract_parties(scanner, counterparties, full_names, result)

    # Quarter shorthand ("Q3 2027", or with the shape sitting in between,
    # "Q3 HL 2027") sets the flow-date range to that quarter's first and
    # last day. Run before price/index extraction so a bare 'for Q3 2027'
    # isn't mistaken for a pricing index, and consume around any embedded
    # shape token rather than swallowing it, so it's still there for the
    # shape extractor below.
    m = re.search(
        r"\b(?:for\s+)?q([1-4])\s*(hl|ll|atc)?\s*(\d{4})\b", scanner.remaining, re.I
    )
    if m:
        start, end = _quarter_bounds(int(m.group(1)), int(m.group(3)))
        result.fields["start_date"] = ParsedField(start, m.group(0).strip(), "derived")
        result.fields["end_date"] = ParsedField(end, m.group(0).strip(), "derived")
        if m.group(2):
            scanner._consume(m.start(), m.start(2))
            scanner._consume(m.end(2), m.end())
        else:
            scanner._consume(m.start(), m.end())

    # Month-range shorthand ("Jul-Aug 28") sets the flow-date range to the
    # first day of the first month through the last day of the second, in
    # the given year. A single month ("Jul 28") is the same idea over one
    # month instead of two.
    m = scanner.take(_MONTH_RANGE_PATTERN)
    if m:
        month1 = MONTH_NAMES[m.group(1).lower()]
        month2 = MONTH_NAMES[m.group(2).lower()]
        year = _full_year(m.group(3))
        start, end = _month_range_bounds(month1, month2, year)
        result.fields["start_date"] = ParsedField(start, m.group(0).strip(), "derived")
        result.fields["end_date"] = ParsedField(end, m.group(0).strip(), "derived")
    else:
        m = scanner.take(_SINGLE_MONTH_PATTERN)
        if m:
            month = MONTH_NAMES[m.group(1).lower()]
            year = _full_year(m.group(2))
            start = date(year, month, 1)
            end = _last_day_of_month(year, month)
            result.fields["start_date"] = ParsedField(start, m.group(0).strip(), "derived")
            result.fields["end_date"] = ParsedField(end, m.group(0).strip(), "derived")

    _extract_price_and_index(scanner, indexes, result)

    m = scanner.take(
        r"\bflow(?:\s+date)?\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"
        r"(?:\s*-\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?)?"
    )
    if m:
        start = _infer_year(int(m.group(1)), int(m.group(2)), today)
        if start is None:
            result.errors.append(f"Could not read the flow date in {m.group(0)!r}.")
        else:
            end = start
            if m.group(4):
                end = _infer_year(int(m.group(4)), int(m.group(5)), today) or start
            result.fields["start_date"] = ParsedField(start, m.group(0).strip())
            result.fields["end_date"] = ParsedField(end, m.group(0).strip())
    else:
        # No numeric flow date: a bare weekday ("Mon only") names a single
        # date directly — the nearest occurrence on or after the trade date.
        wd = scanner.take(_WEEKDAY_PATTERN)
        if wd:
            resolved = _next_weekday_on_or_after(today, WEEKDAY_NAMES[wd.group(1).lower()])
            result.fields["start_date"] = ParsedField(resolved, wd.group(0).strip(), "derived")
            result.fields["end_date"] = ParsedField(resolved, wd.group(0).strip(), "derived")

    # A flow range spanning a full month or more (e.g. a whole quarter) is a
    # monthly trade, regardless of how the dates were derived.
    start_date, end_date = result.get("start_date"), result.get("end_date")
    if start_date and end_date and (end_date - start_date).days + 1 >= 28:
        result.fields["is_monthly"] = ParsedField(
            True, "start_date/end_date span >= a month", "derived"
        )

    # "wspp sched c", but also either word alone ("sched B", "wspp B").
    m = scanner.take(r"\b(?:wspp\s*sched(?:ule)?|sched(?:ule)?|wspp)\s*([bc])\b")
    if m:
        result.fields["wspp_contract"] = ParsedField(
            m.group(1).upper(), m.group(0).strip()
        )

    _extract_segments(scanner, result)

    if scanner.take(r"\bnws\b"):
        result.fields["is_nws"] = ParsedField(True, "nws")
    m = scanner.take(r"\b(?:of\s+)?(?:ncs|non[-\s]?caiso)\b(?:\s+power\b)?")
    if m:
        result.fields["is_source_non_caiso"] = ParsedField(True, m.group(0).strip())

    m = scanner.take(_COMMUNICATION_PATTERN)
    if m:
        value = COMMUNICATION_ALIASES[m.group(1).lower()]
        result.fields["communication"] = ParsedField(value, m.group(0).strip())

    acs = scanner.take(r"\bacs\b")
    ss = scanner.take(r"\bss\b")

    m = scanner.take(r"\bat\s+([a-z0-9._]+)")
    location_token = m.group(1) if m else None

    # Anything still unclaimed: the location if we don't have one, otherwise a
    # named specified source.
    leftover = scanner.leftover_tokens()
    if location_token is None:
        for token in list(leftover):
            value, confidence = resolve_token(token, locations, LOCATION_ALIASES)
            if value is not None:
                result.fields["location"] = ParsedField(value, token, confidence)
                leftover.remove(token)
                break
        else:
            result.errors.append("No location (POR/POD) found in the string.")
    else:
        value, confidence = resolve_token(location_token, locations, LOCATION_ALIASES)
        if value is None:
            result.errors.append(f"Location {location_token!r} not recognized.")
        else:
            result.fields["location"] = ParsedField(value, location_token, confidence)

    for token in list(leftover):
        value, confidence = resolve_token(token, specified_sources)
        if value is not None:
            result.fields["specified_source"] = ParsedField(value, token, confidence)
            leftover.remove(token)
            break

    if acs:
        counterparty = result.get("counterparty")
        source = ACS_SOURCES.get(counterparty)
        if source is None:
            result.errors.append(
                f"'ACS' was used but there is no Asset Controlling Supplier "
                f"mapped for {counterparty or 'this counterparty'}."
            )
        else:
            result.fields["specified_source"] = ParsedField(
                source, f"ACS ({counterparty})", "derived"
            )

    if ss:
        counterparty = result.get("counterparty")
        source = SS_SOURCES.get(counterparty)
        if source is None:
            result.warnings.append(
                f"'SS' was used but no specified source is mapped for "
                f"{counterparty or 'this counterparty'} — set it by hand."
            )
        else:
            result.fields["specified_source"] = ParsedField(
                source, f"SS ({counterparty})", "derived"
            )

    _derive_index_from_location(result)

    # Backstop for any required field that fell through without its own
    # message; the specific errors above are more useful, so don't repeat them.
    already_reported = " ".join(result.errors).lower()
    for name in REQUIRED_FIELDS:
        if name in result.fields:
            continue
        keyword = {"mw": "mw", "direction": "buy/sell"}.get(name, name)
        if keyword in already_reported:
            continue
        result.errors.append(f"Could not find {name.replace('_', ' ')} in the string.")

    for name, parsed in result.fields.items():
        if parsed.confidence == "fuzzy":
            result.warnings.append(
                f"{name.replace('_', ' ').title()} read as {parsed.value!r} "
                f"from {parsed.source_text!r} — close match, worth checking."
            )
    if leftover:
        result.warnings.append(f"Ignored: {' '.join(leftover)}")

    return result
