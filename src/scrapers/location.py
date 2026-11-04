"""Decide whether a job is remote and open to people in the United States.

Reads three kinds of evidence and adds up points:
  1. the location field ("Remote - USA", "Remote (Poland)", "Austin, TX"),
  2. what the source tells us (remote-only job boards, structured work-mode fields),
  3. clues in the posting text ("must reside in the US", "EST hours", "401(k)",
     "salary in EUR", "3 days a week in the office").
Each job ends up remote_us = "yes", "no", or "unsure", with a note saying why.
"""
import re
from dataclasses import dataclass

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "puerto rico",
}
US_CITIES = {"san francisco", "seattle", "austin", "boston", "chicago", "denver", "atlanta",
             "los angeles", "nyc", "new york city", "raleigh", "durham", "charlotte", "dallas",
             "houston", "miami", "portland", "san diego", "san jose", "salt lake city", "phoenix",
             "minneapolis", "pittsburgh", "philadelphia", "nashville", "detroit", "palo alto"}
US_ABBR = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
           "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
FOREIGN_AREAS = [
    # regions that do not include the US
    "emea", "europe", "european union", "eu", "eea", "latam", "latin america", "south america",
    "central america", "apac", "asia", "asia pacific", "africa", "middle east", "oceania", "mena",
    "nordics", "dach", "benelux", "balkans", "cee", "uk", "united kingdom", "great britain", "england",
    "scotland", "wales", "northern ireland",
    # countries
    "canada", "mexico", "brazil", "argentina", "chile", "colombia", "peru", "uruguay", "paraguay",
    "ecuador", "bolivia", "venezuela", "costa rica", "panama", "guatemala", "honduras", "el salvador",
    "nicaragua", "dominican republic", "jamaica", "belize", "trinidad", "ireland", "germany", "france",
    "spain", "portugal", "italy", "netherlands", "belgium", "luxembourg", "switzerland", "austria",
    "poland", "czechia", "czech republic", "slovakia", "hungary", "romania", "bulgaria", "greece",
    "croatia", "serbia", "slovenia", "bosnia", "montenegro", "albania", "north macedonia", "moldova",
    "ukraine", "belarus", "russia", "lithuania", "latvia", "estonia", "finland", "sweden", "norway",
    "denmark", "iceland", "malta", "cyprus", "turkey", "turkiye", "israel", "egypt", "morocco",
    "tunisia", "nigeria", "kenya", "ghana", "south africa", "ethiopia", "uganda", "rwanda", "india",
    "pakistan", "bangladesh", "sri lanka", "nepal", "china", "hong kong", "taiwan", "japan", "korea",
    "south korea", "singapore", "malaysia", "indonesia", "philippines", "vietnam", "thailand",
    "australia", "new zealand", "uae", "united arab emirates", "saudi arabia", "qatar", "armenia",
    "kazakhstan", "uzbekistan",
]
FOREIGN_CITIES = [  # common non-US tech hubs
    "london", "berlin", "munich", "hamburg", "paris", "amsterdam", "dublin", "madrid", "barcelona",
    "lisbon", "warsaw", "krakow", "kraków", "wroclaw", "prague", "budapest", "bucharest", "sofia",
    "vienna", "zurich", "stockholm", "copenhagen", "oslo", "helsinki", "tallinn", "toronto",
    "vancouver", "montreal", "ottawa", "calgary", "waterloo", "bangalore", "bengaluru", "hyderabad",
    "pune", "chennai", "mumbai", "delhi", "noida", "gurgaon", "gurugram", "tel aviv", "sydney",
    "melbourne", "tokyo", "seoul", "manila", "sao paulo", "são paulo", "buenos aires", "bogota",
    "bogotá", "mexico city", "guadalajara", "medellin", "medellín", "cape town", "lagos", "nairobi",
    "cairo", "dubai", "kyiv", "lviv", "belgrade", "athens", "milan", "rome", "brussels",
]
FOREIGN = FOREIGN_AREAS + FOREIGN_CITIES
FOREIGN_ALT = "|".join(sorted((re.escape(p) for p in FOREIGN if len(p) > 3), key=len, reverse=True))
US_ALT = r"U\.?S\.?A?\.?|United States(?: of America)?|America|continental U\.?S\.?|contiguous U\.?S\.?|lower 48"
WHO = r"(?:you|candidates?|applicants?|employees?|team members?|must|should|need to|required to|will need to)"

# (pattern, points, label). Positive points mean "open to the US".
REGION_CLUES = [
    (rf"\b{WHO}\b[^.\n]{{0,40}}\b(?:reside|residing|live|living|located|based|resident)\b[^.\n]{{0,25}}"
     rf"\b(?:in|within|across)\s+(?:the\s+)?(?:{US_ALT})\b", 4, "asks candidates to live in the US"),
    (rf"\b(?:{US_ALT})[- ]only\b", 4, "says US only"),
    (rf"\banywhere in the (?:{US_ALT})\b", 4, "says anywhere in the US"),
    (rf"\bremote\b[^.\n]{{0,25}}\b(?:within|in|across|from) the (?:{US_ALT})\b", 4, "remote within the US"),
    (rf"\b(?:authori[sz]ed|eligible|legally able|permitted) to work (?:in|for) (?:the )?(?:{US_ALT})\b", 2,
     "requires US work authorization"),
    (r"\bU\.?S\.? (?:work authori[sz]ation|citizens?(?:hip)?|persons?|residents?)\b|\bgreen card\b|"
     r"\bsecurity clearance\b", 2, "requires US citizenship or clearance"),
    # Company-wide US legal boilerplate shows up even in postings for other countries, so it counts less.
    (r"\bE-Verify\b|\bW-?2\b", 1, "mentions US employment paperwork"),
    (r"\b(?:EST|EDT|CST|CDT|MST|MDT|PST|PDT)\b|\b(?:Eastern|Central|Mountain|Pacific) (?:Standard )?Time\b|"
     r"\bU\.?S\.? time ?zones?\b|\bUTC ?[-−]\s?[4-8]\b", 2, "works US time zones"),
    (r"\b401\s?\(?k\)?", 2, "offers a 401(k)"),
    (r"\bpay transparency\b|\b(?:Colorado|California|New York|Washington)\b[^.\n]{0,30}"
     r"\b(?:pay|salary|compensation) (?:range|transparency)\b", 1, "has US pay-transparency text"),
    (rf"\b{WHO}\b[^.\n]{{0,40}}\b(?:reside|residing|live|living|located|based|resident)\b[^.\n]{{0,25}}"
     rf"\b(?:in|within)\s+(?:the\s+)?(?:{FOREIGN_ALT})\b", -4, "asks candidates to live outside the US"),
    (rf"\b(?:only|exclusively)\b[^.\n]{{0,20}}\b(?:open|available|hiring|accepting)\b[^.\n]{{0,30}}"
     rf"\b(?:{FOREIGN_ALT})\b", -4, "only hiring outside the US"),
    (r"\b(?:EU|EEA|UK|European|Canadian|Indian) (?:work (?:permit|authori[sz]ation|visa)|citizens?(?:hip)?)\b|"
     r"\bright to work in the UK\b", -4, "requires non-US work authorization"),
    (rf"\b(?:cannot|can't|unable to|do not|don't|not able to|will not)\b[^.\n]{{0,15}}"
     rf"\b(?:hire|consider|accept|employ)\b[^.\n]{{0,40}}\b(?:{US_ALT})\b", -4, "cannot hire in the US"),
    # Currency codes only count next to an amount ("CAD" alone is usually the design software).
    (r"[€£₹]\s?\d|\b(?:EUR|GBP|INR|CAD|AUD|PLN|BRL|MXN|CHF|SEK|NOK|DKK|ZAR)\s?[$€£]?\d|"
     r"\d[\d,.]*\s?[kK]?\s?(?:EUR|GBP|INR|CAD|AUD|PLN|BRL|MXN|CHF|SEK|NOK|DKK|ZAR)\b",
     -2, "pays in a non-US currency"),
    (r"\b(?:CET|CEST|GMT|BST|IST|AEST|AEDT|EET|EEST|BRT|SGT|JST)\b|\bUTC ?\+ ?\d|"
     r"\b(?:European|Central European|Indian|Australian) (?:time|hours|business hours)\b",
     -2, "works non-US time zones"),
]
WEAK_US_CLUES = [(r"\b(?:veteran status|equal opportunity employer|EEO)\b", "has US-style EEO text"),
                 (r"\bUSD\b|\$\s?\d", "pays in USD")]

MODE_CLUES = [
    (r"\b(?:fully|100%|completely|entirely) remote\b|\bremote[- ]first\b|\bremote[- ]friendly\b|"
     r"\bremote (?:role|position|opportunity|job)\b|\bthis (?:role|position|job) (?:is|can be) "
     r"(?:fully |100% )?remote\b|\bwork(?:ing)? (?:from anywhere|remotely|from home)\b", "remote", 2,
     "text says the role is remote"),
    (rf"\bremote\b[^.\n]{{0,25}}\b(?:within|in|across|from) the (?:{US_ALT})\b|"
     rf"\banywhere in the (?:{US_ALT})\b", "remote", 2, "text says remote within the US"),
    (r"\bhybrid (?:role|position|schedule|work|working|model|arrangement|environment|setup|policy)\b|"
     r"\b\d+\s*(?:-\s*\d+\s*)?days? (?:a|per|each) week (?:in|at|from) (?:the|our|an?) office\b|"
     r"\b(?:in|at) (?:the|our) office \d+ days\b", "hybrid", 3, "text describes a hybrid schedule"),
    (r"\b(?:on-?site|in-office|in office) (?:role|position|5 days|five days|full[- ]time|every day)\b|"
     r"\bthis (?:role|position|job) is (?:based )?(?:on-?site|in[- ]office|in[- ]person)\b|"
     r"\bmust (?:be able to )?(?:commute|report to (?:the|our|an) office)\b|\bnot (?:a )?remote\b|"
     r"\bnot eligible for remote\b", "onsite", 3, "text says the role is in the office"),
]
_REGION = [(re.compile(p, re.I), pts, label) for p, pts, label in REGION_CLUES]
_WEAK = [(re.compile(p, re.I), label) for p, label in WEAK_US_CLUES]
_MODE = [(re.compile(p, re.I), mode, pts, label) for p, mode, pts, label in MODE_CLUES]
_FOREIGN = re.compile(rf"\b(?:{'|'.join(re.escape(p) for p in sorted(FOREIGN, key=len, reverse=True))})\b")
_US_WORDS = re.compile(r"\b(?:united states|u\.s\.a?\.?|usa|us|u\.s|north america|americas)\b")
_US_PLACES = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in sorted(US_STATES | US_CITIES,
                                                                       key=len, reverse=True)) + r")\b")
_US_ABBR = re.compile(r"(?:^|[,\s(/-])(?:" + "|".join(US_ABBR) + r")(?=$|[\s,)/;-])")
_WORLDWIDE = re.compile(r"\b(?:worldwide|anywhere|global|globally|any location|international)\b")
_REMOTE_LOC = re.compile(r"\b(?:remote|work from home|wfh|distributed|flexible)\b")
_HYBRID_LOC = re.compile(r"\bhybrid\b")
_OFFICE_LOC = re.compile(r"\b(?:in-?office|on-?site|in[- ]person|hq|headquarters)\b")
_FOREIGN_CITY = re.compile(rf"\b(?:{'|'.join(re.escape(p) for p in FOREIGN_CITIES)})\b")


@dataclass
class Verdict:
    """The classifier's answer for one job."""
    work_mode: str      # remote | hybrid | onsite | unknown
    us_eligible: str    # yes | no | unsure
    remote_us: str      # yes | no | unsure
    note: str           # short human-readable reason


# ---------------------------------------------------------------- location field

def read_location(location: str) -> tuple[int, str, str]:
    """Return (region points, work-mode word from the location, note) for the location field."""
    raw = location or ""
    text = raw.lower()
    without_us = _US_PLACES.sub(" ", text)  # so "New Mexico" is not read as Mexico
    foreign = _FOREIGN.findall(without_us)
    us_strong = bool(_US_WORDS.search(text) or _US_PLACES.search(text))
    us_weak = bool(_US_ABBR.search(raw))
    mode = "hybrid" if _HYBRID_LOC.search(text) else "remote" if _REMOTE_LOC.search(text) else ""
    # A city, a state, or "In-Office" points to an office. A bare country ("United States") does not.
    if not mode and (_US_PLACES.search(text) or us_weak or _OFFICE_LOC.search(text)
                     or _FOREIGN_CITY.search(without_us)):
        mode = "office"
    shown = f"location '{raw.strip()[:60]}'"
    if us_strong:
        return (4 if foreign else 5), mode, f"{shown} includes the US"
    if foreign:
        return -5, mode, f"{shown} is outside the US"
    if us_weak:
        return 3, mode, f"{shown} looks like a US state"
    if _WORLDWIDE.search(text):
        return 2, mode or "remote", f"{shown} is worldwide"
    return 0, mode, ""


# ---------------------------------------------------------------- posting text

def region_clues(text: str) -> tuple[int, list[str]]:
    """Points and labels from the description. Each clue counts once; weak clues add at most 1."""
    points, labels = 0, []
    for pattern, pts, label in _REGION:
        match = pattern.search(text)
        if match:
            points += pts
            labels.append(f"{label} (\"{match.group(0).strip()[:50]}\")")
    weak = [label for pattern, label in _WEAK if pattern.search(text)]
    if weak:
        points += 1
        labels.append(weak[0])
    return points, labels


def mode_votes(text: str, location_mode: str, remote_board: bool, hint: str | None
               ) -> tuple[dict[str, int], list[str]]:
    """Points for remote / hybrid / onsite from every source of evidence."""
    votes = {"remote": 0, "hybrid": 0, "onsite": 0}
    labels = []
    if remote_board:
        votes["remote"] += 3
        labels.append("posted on a remote-only job board")
    if location_mode == "office":
        votes["onsite"] += 2
    elif location_mode:
        votes[location_mode] += 4  # an explicit "Remote"/"Hybrid" in the location beats boilerplate
    if hint in votes:
        votes[hint] += 2
        labels.append(f"source marks it {hint}")
    for pattern, mode, pts, label in _MODE:
        if pattern.search(text):
            votes[mode] += pts
            labels.append(label)
    return votes, labels


def pick_mode(votes: dict[str, int]) -> str:
    """Winning work mode, or 'unknown' when evidence is thin or conflicting."""
    ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_pts), (second, second_pts) = ranked[0], ranked[1]
    if top_pts < 2:
        return "unknown"
    # Hybrid vs onsite is not a real conflict (both mean "not remote"); only doubt about remote is.
    close = second_pts >= 2 and top_pts - second_pts <= 1
    if close and "remote" in (top, second):
        return "unknown"
    return top


# ---------------------------------------------------------------- main entry

def classify(location: str, title: str, description: str, remote_board: bool = False,
             mode_hint: str | None = None) -> Verdict:
    """Classify one job as remote-and-US (yes), not (no), or unsure."""
    text = f"{title}\n{description or ''}"
    loc_points, loc_mode, loc_note = read_location(location)
    text_points, text_labels = region_clues(text)
    region_points = loc_points + text_points
    us = "yes" if region_points >= 2 else "no" if region_points <= -2 else "unsure"

    votes, mode_labels = mode_votes(text, loc_mode, remote_board, mode_hint)
    mode = pick_mode(votes)

    if us == "no" or mode in ("hybrid", "onsite"):
        remote_us = "no"
    elif mode == "remote" and us == "yes":
        remote_us = "yes"
    else:
        remote_us = "unsure"
    evidence = [n for n in [loc_note, *text_labels, *mode_labels] if n]
    return Verdict(mode, us, remote_us, summarize(mode, us, evidence))


def summarize(mode: str, us: str, evidence: list[str]) -> str:
    """'Remote, open to US: location 'Remote - USA' includes the US; ...'"""
    mode_text = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite",
                 "unknown": "Unclear if remote"}[mode]
    us_text = {"yes": "open to US", "no": "not open to US", "unsure": "unclear if open to US"}[us]
    detail = "; ".join(evidence[:4]) or "no location clues found"
    return f"{mode_text}, {us_text}: {detail}"


ACCEPTED_MODES = {"remote_only": {"remote"}, "remote_or_hybrid": {"remote", "hybrid"},
                  "any": {"remote", "hybrid", "onsite"}}


def bucket(job: dict, remote_preference: str) -> str:
    """'main', 'unsure', or 'excluded' for this job given the user's work-arrangement preference."""
    accepted = ACCEPTED_MODES.get(remote_preference, {"remote"})
    if job["us_eligible"] == "no":
        return "excluded"
    if job["remote_type"] == "unknown":
        return "unsure"
    if job["remote_type"] not in accepted:
        return "excluded"
    return "unsure" if job["us_eligible"] == "unsure" else "main"
