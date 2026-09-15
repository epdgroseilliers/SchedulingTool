"""Option lists and constants the desk and back office use.

Fill the lists in with real values as they're needed — the order here is the
order shown in each dropdown. Every entry is a plain string. The dropdowns
that use these also accept a typed-in value (`accept_new_options=True`), so
a name missing from a list never blocks a trade — it just isn't offered as
a choice.
"""

from data.bilateral import TIME_ZONES

COUNTERPARTIES = [
    "AZPS", "ABEX", "BPAT", "SCLM", "EPE", "NEVP", "PACE", "PNM", "WALC", "PSCO", "SRP",
    "TNSK", "CONC", "EEMU", "AVAT", "ATOP", "GPM", "P66T", "SCET", "DYNP",
    "ENKP", "HRTL", "TEA", "MCPI", "BHP", "IID", "CSUM", "MEAI", "BEPC", "BPEC",
    "UMPA", "IPCM", "MSCG", "DECM", "CORP", "NWDS", "DGTM", "TEPM", "REMC", "SCP",
    "CCG", "CHPM", "UNSE", "MID", "PRPA", "SMUD", "VTOL", "CEM", "BURB",
    "EPCR", "TEMU", "TPWP", "AEPC", "PGEM", "TIDS", "DRWT", "BRTM", "NRG", "EAGL",
    "LAWM", "LAC1A", "CAISO",
]
LOCATIONS = [
    "PALOVERDE500", "MIDCRemote", "MEAD230", "M345", "LAM345", "MATL.NWMT",
    "SPRINGER345", "AU", "MDWP", "MIDW", "DJ", "NAVAJO500", "MALIN500", "Boundary",
    "JEFF", "JOHNDAY", "LAGRANDE", "FOURCORNE345", "LOLO", "MIDC", "BPAT.NWMT",
    "MCWEST.NWMT", "AMRAD345", "PNPKWALC230", "BPAPUNSCHD", "EDDY230", "BRDY",
    "BC.US.Border", "WESTWING500", "Sylmar", "BigEddy", "SPRINGER345", "GLENCANYON2",
    "WWA", "AB.MT.MATL", "BPAPOWER", "SLATT230", "CROSSOVER", "JBSN", "YTP",
    "ARLINGTONWIND", "AVAT.NWMT", "BPAT.GCPD", "COLSTRIP", "KERR", "REDB", "PACE",
    "AVA.BPAT", "BPAT.PGE", "GLWND1",
]
INDEXES = ["PALOVERDE", "MONA", "MEAD230", "AESO", "MIDC", "CAISO MALIN DA"]
COMMUNICATION_METHODS = [
    None, "ICE", "ICE Chat", "Broker - BGC", "ITAP", "Broker - Equus",
    "Phone - Thomas", "Phone - Charles", "Phone - Emilio", "Phone - Byron",
    "Broker - Tullett", "Broker - ChoicePower", "EnelX",
    "Replacement Tag", "Email",
]
WSPP_CONTRACT_TYPES = ["C", "B"]
SPECIFIED_SOURCES = [
    None,
    "Bonneville Power Administration",
    "Palo Verde Nuclear",
    "Boundary Dam Hydro",
    "Lucky Peak Power Plant",
    "Kerr Hydro",
    "Headgate Rock Hydro",
    "Tacoma Power - ACS",
    "Seattle City Light - ACS",
    "Lake Chelan Hydro",
    "Mid-C Hydro - Rock Island (Chelan County PUD)",
    "Mid-C Hydro - Rocky Reach (Chelan County PUD)",
    "Oxbox-Brownlee - Idaho Power",
]

DEFAULT_COMMUNICATION = None  # matches the placeholder entry in COMMUNICATION_METHODS
DEFAULT_WSPP_CONTRACT = "C"
DEFAULT_SPECIFIED_SOURCE = None

# Every broker string is pasted from ICE Chat, so this is constant rather
# than a per-trade choice.
PARSED_COMMUNICATION = "ICE Chat"

# The desk only trades Pacific Prevailing Time, so this isn't a per-trade
# choice — asserted against the DB's enum so a change there can't silently
# write an invalid value.
TIME_ZONE = "PPT"
assert TIME_ZONE in TIME_ZONES, f"{TIME_ZONE!r} is not one of {TIME_ZONES}"

# POR/PODs that legitimately settle against the MIDC index. The macro read
# these from the sheet's AN5:AN27 range; fill them in here to turn the MIDC
# coherence warning on (an empty list just skips that one check).
MIDC_POR_PODS = ["JOHNDAY", "BC.US.BORDER", "BPAT.NWMT", "MATL.NWMT", "COLSTRIP", "AVAT.NWMT"]

# Pricing node -> POR/PODs that normally go with it. A mismatch is only a
# warning: the macro asked "continue?" rather than refusing.
PRICING_NODE_POR_PODS = {
    "PALOVERDE": ["PALOVERDE500"],
    "MEAD230": ["MEAD230"],
    "MONA": ["MDWP"],
    "MIDC": MIDC_POR_PODS,
}

# Back office fields that are almost never moved off their defaults. They sit
# behind a guard in the "Other attributes" expander, and their values are
# held in session_state (not widget state) so collapsing the guard can't
# discard an edit.
RARE_FIELD_DEFAULTS = {
    "resupply_id": "",
    "secondary_por_pod": "",
    "resource_adequacy_id": "",
    "exchange_id": "",
    "is_option": False,
    "is_monthly": False,
}
RARE_FIELD_LABELS = {
    "resupply_id": "ResupplyID",
    "secondary_por_pod": "Secondary POR/POD",
    "resource_adequacy_id": "ResourceAdequacyID",
    "exchange_id": "ExchangeID",
    "is_option": "IsOption",
    "is_monthly": "IsMonthly",
}


def default_index(options, default):
    """Position of `default` in `options`, or None if it isn't there.

    Returning None leaves the dropdown empty rather than silently selecting
    whatever happens to sit at index 0 — so dropping a default out of an
    option list shows up as a blank field instead of a wrong value.
    """
    try:
        return options.index(default)
    except ValueError:
        return None
