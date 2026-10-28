"""Remove only bounded, separate final politeness clauses for rule parsing."""
import re


_FINAL_POLITE = re.compile(
    r'(?:[，,；;。!！?？]|(?<![0-9０-９])\.)'
    r'\s*(?:谢谢(?:你|您)?|多谢|麻烦了|辛苦了)[\s。.!！?？]*$'
)


def strip_polite_tail(text: str) -> str:
    result = text
    for _ in range(2):
        match = _FINAL_POLITE.search(result)
        if match is None:
            break
        remaining = result[:match.start()].rstrip()
        if not remaining:
            break
        result = remaining
    return result
