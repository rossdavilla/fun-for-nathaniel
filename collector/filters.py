"""Decide which events suit a 5-year-old and fit the family's free time.

These keyword rules are the stage-one stand-in for AI ranking.
"""

import re
from datetime import date, timedelta
from difflib import SequenceMatcher

from .parsers import CHILDRENS_PROGRAM

KID_AGE = 5  # Nathaniel's age: update every birthday.


def _pattern(*terms):
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.I)


NOT_AN_EVENT = _pattern(r"bookmobile(?!:)", r"library system closed", r"libraries closed",
                        r"closure", r"cancell?ed")
NOT_FOR_KIDS = _pattern(
    r"adults?", r"seniors?", r"teens?", r"t\(w\)een\b.*", r"tweens?", r"bunco", r"happy hour", r"wine", r"beers?", r"brew\w*",
    r"cocktails?", r"bar crawl", r"21\+", r"18\+", r"lectures?", r"book club", r"jazz night",
    r"line dancing", r"gala", r"luncheon", r"members dinner", r"meetings?", r"board of",
    r"council", r"optometry", r"tax", r"medicare", r"genealogy", r"veterans? day",
)
UNDER_FIVE_ONLY = _pattern(r"bab(?:y|ies)", r"infants?", r"toddlers?", r"tiny tales", r"preschool\w*",
                           r"wiggle worms", r"wee move")

BEST_FIT = _pattern(
    r"kids?", r"child(?:ren|ren's)?", r"famil(?:y|ies)", r"all ages", r"story ?times?", r"santa",
    r"puppets?", r"zoo", r"petting", r"lego", r"science night", r"polar express", r"pumpkin patch",
    r"egg hunt", r"trunk or treat", r"bounce",
)
GOOD_FIT = _pattern(
    r"parades?", r"tree lighting", r"festival of lights", r"nutcracker", r"holiday (?:\w+ )?market",
    r"christmas (?:festival|market|bazaar)", r"carnival", r"open cockpit", r"dog show",
    r"winnie the pooh", r"cat in the hat", r"beauty and the beast", r"fraggle rock",
    r"crafts?", r"windsock", r"magnet tiles", r"building fun",
)

INTERESTS = {
    "animals": _pattern(r"zoo", r"farms?", r"petting", r"animals?", r"wildlife", r"pon(?:y|ies)",
                        r"dogs?", r"birds?", r"nature walk", r"winnie the pooh"),
    "science": _pattern(r"science", r"stem", r"steam", r"dino\w*", r"build it", r"museum", r"aviation", r"air ?planes?", r"planes?",
                        r"trains?", r"trucks?", r"robots?", r"lego", r"open cockpit",
                        r"polar express", r"air museum"),
    "arts": _pattern(r"story ?times?", r"puppets?", r"crafts?", r"paint\w*", r"art", r"arts",
                     r"music\w*", r"concerts?", r"nutcracker", r"theat(?:er|re)", r"ballet",
                     r"sing\w*", r"read\w*", r"books?", r"bookmark", r"rainbow magic",
                     r"cat in the hat", r"windsock", r"coloring", r"movie", r"kind cards",
                     r"cardmaking", r"collages?", r"journals?", r"ornaments?", r"slime",
                     r"crafternoon", r"snow globes?", r"sticker\w*"),
    "festival": _pattern(r"parades?", r"santa", r"christmas", r"holiday", r"festivals?",
                         r"tree lighting", r"carnival", r"fair", r"markets?", r"skat\w+",
                         r"bounce", r"egg hunt", r"trunk or treat", r"games?", r"play\w*",
                         r"chess"),
}

LATEST_BREAK_DAY_HOUR = 17
MAX_SPAN_DAYS = 14


def fit_for_kid(event):
    """Return "great", "good", or None when the event shouldn't be listed."""
    title = event.title
    if NOT_AN_EVENT.search(title) or NOT_FOR_KIDS.search(title) or UNDER_FIVE_ONLY.search(title):
        return None
    if event.ages and not event.ages[0] <= KID_AGE <= event.ages[1]:
        return None
    if BEST_FIT.search(title):
        return "great"
    if GOOD_FIT.search(title) or CHILDRENS_PROGRAM in event.categories:
        return "good"
    return None


def interest_tags(event):
    text = " ".join([event.title, event.venue, " ".join(event.categories)])
    return [name for name, pattern in INTERESTS.items() if pattern.search(text)]


def free_day_name(day, free_days):
    for period in free_days:
        if date.fromisoformat(period["start"]) <= day <= date.fromisoformat(period["end"]):
            return period["name"]
    return None


def availability(event, free_days):
    """Return (is_available, reason). Weekends count all day, break weekdays until 5 pm."""
    last = (event.end or event.start).date()
    span = min((last - event.start.date()).days, MAX_SPAN_DAYS)
    daytime = event.all_day or event.start.hour < LATEST_BREAK_DAY_HOUR
    for offset in range(max(span, 0) + 1):
        day = event.start.date() + timedelta(days=offset)
        if day.weekday() >= 5:
            return True, "Weekend"
        name = free_day_name(day, free_days)
        if name and daytime:
            return True, name
    return False, "School-week"


def _normalized(title):
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def is_duplicate(a, b):
    if a.start.date() != b.start.date() or a.city.lower() != b.city.lower():
        return False
    return SequenceMatcher(None, _normalized(a.title), _normalized(b.title)).ratio() >= 0.85


def remove_duplicates(events):
    """Keep the first listing of each event. The same show in two towns stays separate."""
    kept = []
    for event in events:
        if not any(is_duplicate(event, other) for other in kept):
            kept.append(event)
    return kept
