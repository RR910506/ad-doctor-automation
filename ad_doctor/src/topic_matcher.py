from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "its",
    "new",
    "of",
    "on",
    "the",
    "to",
    "with",
}

ACTION_GROUPS = {
    "launch": {
        "announce",
        "announces",
        "announced",
        "debut",
        "debuts",
        "introduce",
        "introduces",
        "launch",
        "launches",
        "launched",
        "rollout",
        "rolls",
        "unveil",
        "unveils",
        "unveiled",
    },
    "campaign": {
        "ad",
        "advert",
        "campaign",
        "spot",
    },
    "appointment": {
        "appoint",
        "appoints",
        "appointed",
        "elevate",
        "elevates",
        "hire",
        "hires",
        "join",
        "joins",
        "joined",
        "name",
        "named",
        "names",
    },
    "account_win": {
        "aor",
        "account",
        "land",
        "lands",
        "mandate",
        "pitch",
        "pitches",
        "retain",
        "retains",
        "win",
        "wins",
        "won",
    },
    "review": {
        "pits",
        "review",
        "reviews",
        "shot",
        "showdown",
    },
    "award": {
        "award",
        "awards",
        "honor",
        "honours",
        "winner",
        "winners",
    },
    "partnership": {
        "ambassador",
        "collab",
        "collaboration",
        "partner",
        "partners",
        "partnership",
        "sign",
        "signs",
    },
}

ACTION_TOKENS = set().union(*ACTION_GROUPS.values())
NOISE_TOKENS = STOPWORDS | ACTION_TOKENS | {
    "ad",
    "age",
    "ads",
    "are",
    "can",
    "cup",
    "day",
    "fifa",
    "chief",
    "global",
    "brand",
    "how",
    "inside",
    "it",
    "lead",
    "leader",
    "learn",
    "marketing",
    "media",
    "more",
    "news",
    "officer",
    "part",
    "report",
    "says",
    "that",
    "us",
    "week",
    "what",
    "when",
    "world",
    "year",
    "you",
}

GENERIC_ENTITIES = {
    "ad",
    "ad age",
    "ads",
    "asia",
    "brand",
    "brand report",
    "campaign that caught our eye",
    "can",
    "day",
    "fifa world cup",
    "how",
    "inside",
    "part",
    "publish",
    "south asia",
    "south asia agency",
    "us",
    "week",
    "what",
    "when",
    "world",
    "world cup",
}


@dataclass(frozen=True)
class EventSignature:
    event_key: str | None
    entities: set[str]
    actions: set[str]
    numbers: set[str]
    tokens: set[str]


def normalize_topic_text(topic: str) -> str:
    lowered = topic.lower()
    lowered = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", lowered)
    lowered = re.sub(r"https?://\S+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    tokens = [stem_token(token) for token in lowered.split() if token not in STOPWORDS]
    return " ".join(tokens)


def topic_tokens(topic: str) -> set[str]:
    return set(normalize_topic_text(topic).split())


def topic_similarity(left: str, right: str) -> float:
    left_normalized = normalize_topic_text(left)
    right_normalized = normalize_topic_text(right)
    if not left_normalized or not right_normalized:
        return 0.0

    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    token_score = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    sequence_score = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    return (token_score * 0.65) + (sequence_score * 0.35)


def is_same_news_event(left: str, right: str, threshold: float = 0.42) -> bool:
    if conflicting_cannes_context(left, right):
        return False
    if conflicting_roundup_context(left, right):
        return False
    left_signature = event_signature(left)
    right_signature = event_signature(right)
    if left_signature.event_key and left_signature.event_key == right_signature.event_key:
        return True
    if conflicting_numbers(left_signature.numbers, right_signature.numbers):
        return False
    left_tokens = topic_tokens(left)
    right_tokens = topic_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared_tokens = left_tokens & right_tokens
    if strong_signature_match(left_signature, right_signature, shared_tokens):
        return True
    if len(shared_tokens) < 3:
        return False
    return topic_similarity(left, right) >= threshold


def event_signature(topic: str) -> EventSignature:
    normalized_tokens = topic_tokens(topic)
    return EventSignature(
        event_key=named_event_key(topic),
        entities=extract_entities(topic),
        actions=extract_actions(topic),
        numbers=set(re.findall(r"\b(?:20\d{2}|\d+(?:\.\d+)?%?|\$?\d+(?:\.\d+)?[mb]?)\b", topic.lower())),
        tokens={token for token in normalized_tokens if token not in NOISE_TOKENS and len(token) > 1},
    )


def strong_signature_match(
    left: EventSignature,
    right: EventSignature,
    shared_tokens: set[str],
) -> bool:
    shared_entities = left.entities & right.entities
    shared_actions = left.actions & right.actions
    shared_meaningful_tokens = (left.tokens & right.tokens) | {
        token for token in shared_tokens if token not in NOISE_TOKENS
    }
    shared_meaningful_tokens = {
        token for token in shared_meaningful_tokens if not re.fullmatch(r"\d+|20\d{2}", token)
    }
    if len(shared_entities) >= 2 and (shared_actions or len(shared_meaningful_tokens) >= 2):
        return True
    if len(shared_entities) >= 1 and shared_actions and len(shared_meaningful_tokens) >= 2:
        return True
    if "appointment" in shared_actions and len(shared_entities) >= 1 and len(shared_meaningful_tokens) >= 1:
        return True
    if "review" in shared_actions and len(shared_entities) >= 2:
        return True
    if "campaign" in shared_actions and len(shared_entities) >= 1 and len(shared_meaningful_tokens) >= 3:
        return True
    return False


def conflicting_numbers(left_numbers: set[str], right_numbers: set[str]) -> bool:
    left_years = {number for number in left_numbers if re.fullmatch(r"20\d{2}", number)}
    right_years = {number for number in right_numbers if re.fullmatch(r"20\d{2}", number)}
    return bool(left_years and right_years and left_years.isdisjoint(right_years))


def conflicting_cannes_context(left: str, right: str) -> bool:
    left_lowered = left.lower()
    right_lowered = right.lower()
    if "cannes" not in left_lowered or "cannes" not in right_lowered:
        return False
    return is_entertainment_cannes(left_lowered) != is_entertainment_cannes(right_lowered)


def conflicting_roundup_context(left: str, right: str) -> bool:
    return is_roundup_headline(left.lower()) != is_roundup_headline(right.lower())


def is_roundup_headline(lowered: str) -> bool:
    roundup_markers = (
        "ads of the week",
        "campaigns that caught our eye",
        "best ads",
        "top ads",
        "roundup",
    )
    return any(marker in lowered for marker in roundup_markers)


def is_entertainment_cannes(lowered: str) -> bool:
    entertainment_markers = (
        "film festival",
        "red carpet",
        "movie",
        "director",
        "actor",
        "actress",
        "stars",
    )
    return any(marker in lowered for marker in entertainment_markers)


def extract_actions(topic: str) -> set[str]:
    lowered = topic.lower()
    normalized_tokens = topic_tokens(topic)
    actions = set()
    for group, keywords in ACTION_GROUPS.items():
        if normalized_tokens & {stem_token(keyword) for keyword in keywords}:
            actions.add(group)
        if any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords):
            actions.add(group)
    return actions


def extract_entities(topic: str) -> set[str]:
    entities: set[str] = set()
    cleaned = re.sub(r"https?://\S+", " ", topic)
    for match in re.finditer(
        r"\b(?:[A-Z][A-Za-z0-9&'.-]*|[A-Z]{2,})(?:\s+(?:[A-Z][A-Za-z0-9&'.-]*|[A-Z]{2,}))*",
        cleaned,
    ):
        entity = normalize_entity(match.group(0))
        if is_usable_entity(entity):
            entities.add(entity)
    known_mixed_case = (
        "coca-cola",
        "duolingo english test",
        "jw marriott",
        "netcore",
        "publicis",
        "wpp",
    )
    lowered = cleaned.lower()
    for entity in known_mixed_case:
        if entity in lowered:
            normalized_entity = normalize_entity(entity)
            if is_usable_entity(normalized_entity):
                entities.add(normalized_entity)
    return entities


def normalize_entity(entity: str) -> str:
    normalized = normalize_topic_text(entity)
    return " ".join(token for token in normalized.split() if token not in STOPWORDS)


def is_usable_entity(entity: str) -> bool:
    if not entity or len(entity) <= 1:
        return False
    if entity in GENERIC_ENTITIES or entity in NOISE_TOKENS:
        return False
    tokens = set(entity.split())
    return bool(tokens - NOISE_TOKENS)


def named_event_key(topic: str) -> str | None:
    lowered = topic.lower()
    event_patterns = [
        r"\b(cannes\s+lions)\s*(20\d{2})\b",
        r"\b(sxsw)\s*(20\d{2})\b",
        r"\b(coachella)\s*(20\d{2})\b",
    ]
    for pattern in event_patterns:
        match = re.search(pattern, lowered)
        if match:
            return " ".join(match.groups()).strip()
    cannes_year = re.search(r"\bcannes\b.*\b(20\d{2})\b|\b(20\d{2})\b.*\bcannes\b", lowered)
    entertainment_cannes_markers = (
        "film festival",
        "red carpet",
        "movie",
        "director",
        "actor",
        "actress",
    )
    marketing_cannes_markers = (
        "lions",
        "festival programme",
        "festival program",
        "schedule",
        "events",
        "jury",
        "lineup",
        "categories",
        "brand lion",
        "ad festival",
        "cmo",
        "ad tech",
    )
    if (
        cannes_year
        and not any(marker in lowered for marker in entertainment_cannes_markers)
        and any(marker in lowered for marker in marketing_cannes_markers)
    ):
        year = next(group for group in cannes_year.groups() if group)
        return f"cannes lions {year}"
    generic_match = re.search(
        r"\b([a-z][a-z0-9&.'-]+(?:\s+[a-z][a-z0-9&.'-]+){0,3})\s+(festival|awards?|lions|conference|summit)\s*(20\d{2})\b",
        lowered,
    )
    if generic_match:
        return " ".join(generic_match.groups()).strip()
    return None


def find_matching_topic(topic: str, existing_topics: list[dict[str, object]]) -> dict[str, object] | None:
    best_topic = None
    best_score = 0.0
    topic_signature = event_signature(topic)
    topic_signal_tokens = topic_tokens(topic)
    for existing_topic in existing_topics:
        existing_signal = get_topic_match_signal(existing_topic)
        if not topic_signals_overlap(topic_signature, topic_signal_tokens, existing_signal):
            continue
        for candidate in topic_candidate_texts(existing_topic):
            score = topic_similarity(topic, candidate)
            if score > best_score and is_same_news_event(topic, candidate):
                best_score = score
                best_topic = existing_topic
    return best_topic


def get_topic_match_signal(existing_topic: dict[str, object]) -> dict[str, object]:
    cached_signal = existing_topic.get("_match_signal")
    if isinstance(cached_signal, dict):
        return cached_signal
    event_keys = set()
    entities = set()
    tokens = set()
    for candidate in topic_candidate_texts(existing_topic):
        signature = event_signature(candidate)
        if signature.event_key:
            event_keys.add(signature.event_key)
        entities.update(signature.entities)
        tokens.update(topic_tokens(candidate))
    signal = {"event_keys": event_keys, "entities": entities, "tokens": tokens}
    existing_topic["_match_signal"] = signal
    return signal


def topic_signals_overlap(
    topic_signature: EventSignature,
    topic_signal_tokens: set[str],
    existing_signal: dict[str, object],
) -> bool:
    event_keys = existing_signal.get("event_keys")
    if (
        topic_signature.event_key
        and isinstance(event_keys, set)
        and topic_signature.event_key in event_keys
    ):
        return True
    entities = existing_signal.get("entities")
    if isinstance(entities, set) and topic_signature.entities & entities:
        return True
    tokens = existing_signal.get("tokens")
    if isinstance(tokens, set) and len(topic_signal_tokens & tokens) >= 2:
        return True
    return False


def topic_candidate_texts(existing_topic: dict[str, object]) -> list[str]:
    candidates = [
        str(existing_topic.get("exact_topic") or ""),
        str(existing_topic.get("representative_headline") or ""),
    ]
    aliases = existing_topic.get("aliases") or []
    if isinstance(aliases, list):
        candidates.extend(str(alias) for alias in aliases)
    return [candidate for candidate in dict.fromkeys(candidates) if candidate]


def stem_token(token: str) -> str:
    if len(token) > 5 and token.endswith(("ing", "ed")):
        return token[:-3] if token.endswith("ing") else token[:-2]
    if len(token) >= 4 and token.endswith("ses"):
        return token[:-1]
    if len(token) >= 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def match_topics() -> None:
    raise NotImplementedError("Use find_matching_topic or is_same_news_event.")
