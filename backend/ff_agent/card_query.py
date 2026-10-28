"""Pure helpers for matching queries against caller-supplied cards."""

import re
import unicodedata


def normalize_card_text(text):
    """Apply NFKC and remove format characters, preserving other content."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Cf"
    ).strip()


def resolve_card_query(cards, query, recent_card_id=None):
    """Return all matching original card objects without changing inputs."""
    text = normalize_card_text(query).strip()
    if text in {"", "未指定", "全部", "unknown", "卡", "银行卡"}:
        return []

    if text in {"刚才那张", "这张", "那张", "上次那张"}:
        if recent_card_id is None:
            return []
        return [card for card in cards if card['id'] == recent_card_id]

    tail_match = re.fullmatch(r"(?:卡尾号|尾号)?\s*([0-9]{4})", text)
    if tail_match is not None:
        tail = tail_match.group(1)
        return [
            card for card in cards
            if (match := re.search(r'(?<![0-9])([0-9]{4})$',normalize_card_text(card['masked_number']).strip()))
            and match.group(1)==tail
        ]

    aliases = {
        "日常": "日常",
        "日常卡": "日常",
        "日常账户": "日常",
        "日常那张": "日常",
        "储蓄": "储蓄",
        "储蓄卡": "储蓄",
        "储蓄账户": "储蓄",
        "储蓄那张": "储蓄",
    }
    keyword = aliases.get(text)
    if keyword is not None:
        return [
            card for card in cards
            if keyword in normalize_card_text(card['name'])
        ]

    return [
        card for card in cards
        if normalize_card_text(card['name']).strip() == text
    ]
