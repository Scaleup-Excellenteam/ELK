"""Convert text typed with the Hebrew layout into its English keys."""

HEBREW_TO_ENGLISH_KEYS = str.maketrans(
    {
        "ק": "e",
        "ר": "r",
        "א": "t",
        "ט": "y",
        "ו": "u",
        "ן": "i",
        "ם": "o",
        "פ": "p",
        "ש": "a",
        "ד": "s",
        "ג": "d",
        "כ": "f",
        "ע": "g",
        "י": "h",
        "ח": "j",
        "ל": "k",
        "ך": "l",
        "ף": ";",
        "ז": "z",
        "ס": "x",
        "ב": "c",
        "ה": "v",
        "נ": "b",
        "מ": "n",
        "צ": "m",
        "ת": ",",
        "ץ": ".",
        "'": "w",
        "/": "q",
    }
)


def contains_hebrew(text: str) -> bool:
    """Return whether text contains a Hebrew character."""
    return any("\u0590" <= character <= "\u05ff" for character in text)


def convert_hebrew_keyboard_layout(text: str) -> str:
    """Map Hebrew-layout keys to their English QWERTY equivalents."""
    if not contains_hebrew(text):
        return text
    return text.translate(HEBREW_TO_ENGLISH_KEYS)
