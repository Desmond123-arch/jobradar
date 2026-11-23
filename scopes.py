"""Where a remote job can be done from, as the kinds of role you can tick, the way job sites let you filter.

A role belongs to every category its location wording supports, and you see it if it belongs to any category you ticked:

  worldwide     "Worldwide", "Anywhere", "Global": open to anyone
  africa        names Africa, or a country or city in it
  emea          says EMEA (Europe, the Middle East and Africa). That includes Africa, Europe and the Middle East,
                so an EMEA role is also open to someone in any of those three
  europe        names Europe, the UK, or a European country or city
  middle-east   names the Middle East, or a country or city there
  americas      names the US, Canada or Latin America
  asia-pacific  names Asia, Australia, or a country there
  my-timezone   ties its hours to a time zone that suits yours (decided in timezones.py, not here)
  unclear       just says "Remote" without naming a place

Nothing here is a promise. It is wording matching: "Remote - Germany" is read as Europe, and a posting can still be
wrong about what it means. Open it and check before you apply.
"""

import re

# key, label, what it means (shown beside the tick box), badge style
CATEGORIES = [
    ("worldwide", "Worldwide", "roles open to anyone, anywhere", "ok"),
    ("africa", "Africa", "roles that name Africa, or a country or city in it", "ok"),
    ("emea", "EMEA", "roles that say EMEA (Europe, Middle East and Africa)", "ok"),
    ("europe", "Europe", "roles that name Europe, the UK, or a European country or city", "ok"),
    ("middle-east", "Middle East", "roles that name the Middle East, or a country or city there", "ok"),
    ("americas", "Americas", "roles that name the US, Canada or Latin America", "ok"),
    ("asia-pacific", "Asia-Pacific", "roles that name Asia, Australia, or a country there", "ok"),
    ("my-timezone", "My time zone", "roles that tie their hours to a time zone near yours", "ok"),
    ("unclear", "Don't say where", 'roles that just say "Remote" without naming a place', "warn"),
]
KEYS = [c[0] for c in CATEGORIES]
LABELS = {k: label for k, label, _, _ in CATEGORIES}
BADGE = {"unclear": "Check location"}  # what the badge says, where it differs from the category name
DEFAULT = ["worldwide", "africa", "emea", "my-timezone", "unclear"]
# The wider set the old "all of EMEA" choice meant
EMEA_WIDE = ["worldwide", "africa", "emea", "europe", "middle-east", "my-timezone", "unclear"]
# Which label a role shows when it fits several: the most specific first.
PRIORITY = ["africa", "emea", "my-timezone", "europe", "middle-east", "americas", "asia-pacific", "worldwide", "unclear"]

# Statements that anyone can apply, and softer ones ("fully remote") that only mean there is no office.
# A soft one does not count beside a named place: "Fully Remote (US-only)" is a US job.
WORLDWIDE_STRONG = re.compile(r"\b(anywhere|worldwide|world ?wide|all countries|any (country|location))\b", re.I)
WORLDWIDE_SOFT = re.compile(r"\b(global(ly)?|fully remote|remote[- ]first)\b", re.I)
DESC_LOCK = re.compile(
    r"(must (be|reside|live) (based )?in (the )?(us|u\.s\.|united states|canada|uk|europe|eu)|"
    r"authori[sz]ed to work in the (us|u\.s\.|united states|uk)|"
    r"(us|u\.s\.)[- ]based (only|candidates)|only (accepting|considering) (applicants|candidates) (from|in|based in)|"
    r"within the (us|u\.s\.|united states|eu|uk)|(us|est|pst|cet) time ?zone (only|required)|"
    r"we (can|are able to) (only )?hire in)", re.I)

EMEA_WORD = re.compile(r"\bemea\b|\beurope,? (?:the )?middle east(?:,? (?:and|&) africa)?\b|\beurope ?[/&] ?africa\b", re.I)

AFRICA = re.compile(
    r"\b(africa|african|sub-saharan|nigeria|lagos|abuja|ghana|accra|kumasi|kenya|nairobi|mombasa|south africa|cape town|"
    r"johannesburg|pretoria|durban|egypt|cairo|morocco|casablanca|rabat|tunisia|tunis|algeria|algiers|ethiopia|addis ababa|"
    r"tanzania|dar es salaam|uganda|kampala|rwanda|kigali|senegal|dakar|ivory coast|c[oô]te d'ivoire|abidjan|cameroon|"
    r"douala|zambia|lusaka|zimbabwe|harare|botswana|gaborone|namibia|windhoek|mauritius|angola|luanda|mozambique|maputo|"
    r"malawi|benin|togo|mali|burkina faso|sierra leone|liberia|gambia|congo|drc|sudan|libya|madagascar)\b", re.I)

EUROPE = re.compile(
    r"\b(europe|european|eu|eea|uk|u\.k\.|gb|great britain|united kingdom|england|scotland|wales|ireland|germany|france|"
    r"spain|portugal|italy|netherlands|belgium|luxembourg|switzerland|austria|poland|czech(?:ia| republic)?|slovakia|"
    r"hungary|romania|bulgaria|greece|croatia|serbia|slovenia|estonia|latvia|lithuania|finland|sweden|norway|denmark|"
    r"iceland|ukraine|cyprus|malta|london|dublin|berlin|munich|hamburg|frankfurt|paris|amsterdam|rotterdam|brussels|"
    r"madrid|barcelona|lisbon|porto|rome|milan|zurich|geneva|vienna|warsaw|krakow|prague|budapest|bucharest|sofia|"
    r"athens|stockholm|copenhagen|oslo|helsinki|tallinn|riga|vilnius|edinburgh|manchester|belfast|cardiff|utrecht)\b", re.I)

MIDDLE_EAST = re.compile(
    r"\b(middle east|mena|uae|united arab emirates|dubai|abu dhabi|saudi(?: arabia)?|riyadh|jeddah|qatar|doha|bahrain|"
    r"kuwait|oman|muscat|jordan|amman|lebanon|beirut|israel|tel aviv|turkey|t[uü]rkiye|istanbul|ankara|iraq|iran)\b", re.I)

_US_STATES = ("AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|"
              "OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC")
AMERICAS = re.compile(
    r"\b(us|usa|u\.s\.|u\.s\.a\.|united states|america|north america|americas|canada|canadian|toronto|vancouver|montreal|"
    r"latam|latin america|south america|brazil|mexico|argentina|colombia|chile|peru|new york|san francisco|seattle|"
    r"los angeles|boston|chicago|austin|denver)\b", re.I)
# "Smalltown, TX": a US state code, capitals only. Many codes are also country codes (DE, IN, IL, ID, CO...), so this
# only counts when nothing else in the text named a place: "Berlin, DE" is Germany.
US_STATE = re.compile(rf",\s*(?:{_US_STATES})\b(?!\w)")

ASIA_PACIFIC = re.compile(
    r"\b(apac|asia|asia[- ]pacific|india|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai|singapore|japan|tokyo|"
    r"australia|sydney|melbourne|brisbane|new zealand|auckland|philippines|manila|china|beijing|shanghai|hong kong|korea|"
    r"seoul|indonesia|jakarta|vietnam|thailand|bangkok|malaysia|kuala lumpur|pakistan|karachi|bangladesh|dhaka|taiwan|"
    r"sri lanka|nepal)\b", re.I)

PLACES = {"africa": AFRICA, "europe": EUROPE, "middle-east": MIDDLE_EAST, "americas": AMERICAS, "asia-pacific": ASIA_PACIFIC}


def parse(text):
    """'worldwide, africa' or ['worldwide', 'africa'] as a clean list of category keys, in the standard order.
    Raises ValueError on a name it doesn't know."""
    items = text if isinstance(text, (list, tuple, set)) else re.split(r"[,\s;]+", str(text or ""))
    wanted = [str(i).strip().lower() for i in items if str(i).strip()]
    unknown = [w for w in wanted if w not in KEYS]
    if unknown:
        raise ValueError(f"unknown remote role type {unknown[0]!r}; choose from: {', '.join(KEYS)}")
    return [k for k in KEYS if k in wanted]


def valid(value):
    try:
        return isinstance(value, list) and parse(value) == value and len(set(value)) == len(value)
    except ValueError:
        return False


def short(selected):
    """The selection in a few words, for a list of settings."""
    if not selected:
        return "nothing ticked"
    names = [LABELS[k] for k in KEYS if k in selected]
    return ", ".join(names) if len(names) <= 3 else f"{', '.join(names[:3])} and {len(names) - 3} more"


def of(location, description=""):
    """The categories a remote role's wording supports, and a sentence saying why.
    (a set of keys, a reason). The time zone category is added elsewhere, from the hours it mentions."""
    loc = location or ""
    found, why = set(), []
    geo = {key for key, rx in PLACES.items() if rx.search(loc)}
    if EMEA_WORD.search(loc):
        geo |= {"emea", "africa", "europe", "middle-east"}  # EMEA is all three of them
    if not geo and US_STATE.search(loc):
        geo.add("americas")
    locked = False
    if WORLDWIDE_STRONG.search(loc):
        found.add("worldwide")
    elif WORLDWIDE_SOFT.search(loc) and not geo:
        found.add("worldwide")
    if "worldwide" in found and DESC_LOCK.search(description or ""):
        found.discard("worldwide")
        locked = True
        why.append(f"says worldwide, but the description says: '{DESC_LOCK.search(description).group(0)}'")
    found |= geo
    if not found and locked:
        return found, "; ".join(why)  # worldwide on its face, locked by the description: open to no category
    if not found:
        stripped = re.sub(r"\bremote\b", "", WORLDWIDE_SOFT.sub("", loc), flags=re.I).strip(" ,-–()/|%0123456789")
        if not stripped and DESC_LOCK.search(description or ""):
            why.append(f"remote, but the description says: '{DESC_LOCK.search(description).group(0)}'")
        elif not stripped:
            found.add("unclear")
            why.append('says "Remote" without naming a place; check the description')
        else:
            found.add("unclear")
            why.append(f"remote, place not recognised: {loc}")
    elif not why:
        why.append(f"location: {loc}")
    return found, "; ".join(why)


def label_for(found, selected):
    """The one category a role is shown under: the most specific of the ticked ones it belongs to."""
    mine = [k for k in PRIORITY if k in found and k in selected]
    return mine[0] if mine else None
