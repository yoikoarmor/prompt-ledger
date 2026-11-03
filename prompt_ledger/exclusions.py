"""Literal Unicode case-insensitive substring rules; never regular expressions."""

from .config import LedgerError


def validate_terms(terms):
    if not isinstance(terms, list) or len(terms) > 100:
        raise LedgerError("exclusion_rules", "Supply at most 100 literal strings.")
    result, seen = [], set()
    for term in terms:
        if not isinstance(term, str) or not term.strip() or len(term) > 256 or "\n" in term or "\r" in term:
            raise LedgerError(
                "exclusion_rules",
                "Each rule must be a nonblank single-line string of at most 256 characters.",
            )
        if term.casefold() not in seen:
            result.append(term)
            seen.add(term.casefold())
    return result


def matches(positive, negative, terms):
    positive, negative = (positive or "").casefold(), (negative or "").casefold()
    return [term for term in terms if term.casefold() in positive or term.casefold() in negative]
