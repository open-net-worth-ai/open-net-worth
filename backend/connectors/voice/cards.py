"""What counts as a spoken yes to an approval card, and how a card asks.

A yes is decided here, on the owner's own words, and never by how a model read them: text read out
on a call reaches the front model, so its reading can be talked into anything (security review,
2026-11-07). So it's an allowlist: only an answer made up entirely of these approves. Anything else,
"hmm", a question, half a sentence, a language with no list, approves nothing, and they're asked
again. No bare "okay" or "right": people say those to mean they're following.
"""

from __future__ import annotations

import re

_YES = {
    "en": (
        "yes", "please", "yeah", "yep", "yup", "sure", "correct", "confirm", "confirmed",
        "confirm it", "approve", "approved", "approve it", "go ahead", "go for it", "do it",
        "please do", "yes please", "yes go ahead", "okay go ahead", "ok go ahead", "sure go ahead",
        "yes do it", "yes confirm", "yes approve", "yes approve it", "yes correct", "that's right",
        "that's correct", "sounds good", "looks good",
    ),
    "vi": (
        "có", "vâng", "dạ", "ừ", "đúng", "đúng rồi", "được", "đồng ý", "làm đi", "duyệt đi",
        "xác nhận", "tiếp tục", "vâng làm đi", "có làm đi", "ok làm đi", "dạ vâng",
    ),
    "zh": ("是", "是的", "对", "好", "好的", "可以", "确认", "同意", "批准", "继续"),
    "ja": ("はい", "ええ", "お願いします", "確認します", "承認します", "進めてください"),
    "ko": ("네", "예", "좋아요", "승인", "승인해 주세요", "확인", "진행해 주세요"),
    "hi": ("हाँ", "हां", "जी हाँ", "ठीक है", "कर दो", "मंज़ूर है", "मंजूर है"),
    "id": ("ya", "iya", "betul", "benar", "setuju", "lanjutkan", "silakan", "konfirmasi"),
    "es": (
        "sí", "si", "adelante", "confirmo", "de acuerdo", "correcto", "hazlo", "apruébalo",
        "sí adelante", "sí por favor",
    ),
    "fr": (
        "oui", "d'accord", "je confirme", "vas-y", "allez-y", "oui vas-y", "approuve",
        "oui s'il te plaît", "oui s'il vous plaît",
    ),
    "de": (
        "ja", "bestätigen", "ich bestätige", "genau", "richtig", "ja bitte", "einverstanden",
        "mach das", "weiter",
    ),
    "pt": ("sim", "pode", "pode fazer", "correto", "de acordo", "aprovo", "sim por favor"),
}  # fmt: skip
_CLAUSES = re.compile(r"[.,!;:。，！、।\n]+")
_QUESTION = re.compile(r"[?？؟]")

CARD_QUESTION = {
    "en": "Shall I go ahead?",
    "vi": "Tôi làm nhé?",
    "zh": "要继续吗?",
    "ja": "進めてもよろしいですか?",
    "ko": "진행할까요?",
    "hi": "क्या मैं आगे बढ़ूँ?",
    "id": "Boleh saya lanjutkan?",
    "es": "¿Sigo adelante?",
    "fr": "Je continue ?",
    "de": "Soll ich fortfahren?",
    "pt": "Posso continuar?",
}


def is_plain_yes(said: str, language: str = "en") -> bool:
    """Every part of what they said is a yes, in the call's language or in English, which people
    mix in everywhere, and there's nothing else in it."""
    if _QUESTION.search(said):
        return False
    clauses = [" ".join(c.split()).casefold() for c in _CLAUSES.split(said)]
    clauses = [c.replace("’", "'") for c in clauses if c]
    known = {*_YES["en"], *_YES.get(language, ())}
    return bool(clauses) and all(c in known for c in clauses)


def card_reading(summary: str, language: str) -> str:
    """Said word for word, with no model in between: a card's text can hold a merchant's name or a
    model's words, and through a model those could change what the owner is asked."""
    return f"{summary} {CARD_QUESTION.get(language, CARD_QUESTION['en'])}"
