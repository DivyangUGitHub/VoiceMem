"""Language used for free-form text stored in a memory space.

Memory spaces use a single language so retrieval stays consistent. The language is
stored with the space and can be selected with a BCP 47 language tag.
"""

from __future__ import annotations

import os

ENV = "VOICEMEM_MEMORY_LANGUAGE"
DEFAULT = "en"

LANGUAGE_NAMES = {
    "en": "English", "zh": "Chinese", "hi": "Hindi", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "pt-BR": "Portuguese (Brazil)", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic", "ru": "Russian", "bn": "Bengali", "ur": "Urdu",
    "pa": "Punjabi", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "th": "Thai",
    "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay", "tr": "Turkish",
    "nl": "Dutch", "pl": "Polish", "uk": "Ukrainian", "cs": "Czech",
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
    "el": "Greek", "he": "Hebrew", "fa": "Persian", "ro": "Romanian",
    "hu": "Hungarian", "sw": "Swahili",
}

SUPPORTED = tuple(LANGUAGE_NAMES)
_override: str | None = None


def _check(value: str) -> str:
    v = (value or "").strip().replace("_", "-")
    if not v:
        return DEFAULT
    parts = v.split("-")
    if not (2 <= len(parts[0]) <= 3 and parts[0].isalpha()):
        raise ValueError(f"Invalid memory language tag: {value!r}")
    normalized = parts[0].lower()
    if len(parts) > 1:
        normalized += "".join(
            "-" + (part.title() if len(part) == 4 and part.isalpha()
                   else part.upper() if len(part) == 2 and part.isalpha()
                   else part.lower())
            for part in parts[1:]
        )
    return normalized


def language_name(value: str) -> str:
    tag = _check(value)
    if tag in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[tag]
    base = tag.split("-")[0]
    return LANGUAGE_NAMES.get(base, base)


def _set(lang: str) -> None:
    global _override
    _override = lang


def set_memory_language(value: str | None) -> None:
    """Set the process-level language override."""
    global _override
    _override = _check(value) if (value or "").strip() else None


def memory_language() -> str:
    if _override:
        return _override
    env = (os.environ.get(ENV, "") or "").strip()
    return _check(env) if env else DEFAULT


def resolve_for_space(memory_root, explicit: str | None = None) -> str:
    """Resolve and persist the language associated with a memory space."""
    import json as _json
    from voicemem.utils.common import space as _space

    try:
        path = _space.json_path(memory_root)
    except Exception:
        set_memory_language(explicit)
        return memory_language()

    stored = ""
    try:
        if path.exists():
            stored = (_json.loads(path.read_text(encoding="utf-8"))
                      .get("space", {}).get("language", "") or "")
    except Exception:
        stored = ""

    if explicit:
        lang = _check(explicit)
    elif stored:
        lang = _check(stored)
    else:
        env = (os.environ.get(ENV, "") or "").strip()
        lang = _check(env) if env else DEFAULT

    if lang != stored:
        try:
            doc = (_json.loads(path.read_text(encoding="utf-8"))
                   if path.exists() else {})
            doc.setdefault("space", {})["language"] = lang
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        except Exception as e:
            print(f"[lang] Failed to write space language (non-fatal): {e}", flush=True)

    _set(lang)
    return lang


def is_zh() -> bool:
    return memory_language().split("-")[0] == "zh"


def label_rule() -> str:
    """Return the language instruction used by memory extraction prompts."""
    lang = language_name(memory_language())
    return (f"Write every label in {lang}, whatever language the speaker used. "
            f"Do not mix in any other language.")


# Internal canonical emotion keys remain unchanged because retrieval and graph
# routing depend on them. User-facing non-Chinese spaces use stable English labels.
_EMOTION_EN = {
    "焦虑": "anxious", "悲伤": "sad", "委屈": "wronged", "孤独": "lonely",
    "纠结": "conflicted", "平静": "calm", "开心": "happy", "疲惫": "tired",
}


def display_emotion(canonical: str) -> str:
    """Return a user-facing emotion label for the current memory language."""
    if is_zh():
        return canonical
    return _EMOTION_EN.get(canonical, canonical)
