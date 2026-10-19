"""Small, explicit routing layer for the local demo; not an AI interpreter.

Authority routing follows handbook v0.9 R02/R24. Broader language, negation
and organisation types outside this catalogue require an open-ended chat clarification.
"""
import re

PURPOSES = {
    "signatory": "Change account signatories or signing arrangements",
    "details": "Update business or contact details",
    "phone": "Change Phone Banking",
    "card_new": "Apply for a new debit card",
    "card_existing": "Change or cancel an existing debit card",
}
ORGANISATIONS = {
    "association": "Registered association / club / society",
    "company": "Private limited / public company",
    "other": "Another organisation type",
    "unknown": "I don't know",
}
TITLES = {
    "F01": "Business Account Maintenance",
    "F02": "Board Resolution — Companies",
    "F03": "Minutes of Meeting — Associations",
    "F05A": "Business Debit Card Application",
    "F05M": "Business Debit Card Maintenance",
}


def interpret(text):
    """Recognise clear demo requests; ask rather than interpret complex clauses."""
    value = text.lower().replace("’", "'")
    org = None
    association = bool(re.search(r"\b(registered association|association|society|club)\b", value))
    company = bool(re.search(r"\b(pte\.?\s*ltd\.?|private limited|public company)\b", value))
    if association != company:
        org = "association" if association else "company"
    if re.search(r"\b(llp|limited liability partnership|sole proprietor|partnership)\b", value):
        org = "other"
    # Negation and corrections may change which services apply. Do not guess.
    ambiguous = bool(re.search(r"\b(not|don't|do not|instead|actually|only|no longer|no need|without)\b", value))
    unsupported = bool(re.search(r"\b(velocity|credit card|loan|mortgage|remittance|open an account|open a new account)\b", value))
    needs = []
    if re.search(r"\b(signator\w*|signers?|signing arrangements?|signing conditions?|signing mandate|who can sign)\b", value):
        needs.append("signatory")
    if re.search(r"\b(phone banking|telephone banking)\b", value):
        needs.append("phone")
    if re.search(r"\b(contact details|business details|business address|mailing address|email address)\b", value):
        needs.append("details")
    if "debit card" in value or "debit cards" in value:
        new = bool(re.search(r"\b(apply|application|new debit card|additional card)\b", value))
        existing = bool(re.search(r"\b(cancel|terminate|replace|replacement|existing|lost|stolen|limit|pin|change)\b", value))
        # Replacement cards belong to maintenance, despite the word 'new'.
        if re.search(r"\b(replace|replacement|cancel|terminate|lost|stolen)\b", value):
            existing = True
            if not re.search(r"\b(apply|additional)\b", value):
                new = False
        if new:
            needs.append("card_new")
        if existing and not new:
            needs.append("card_existing")
        if not new and not existing:
            ambiguous = True
        if new and existing:
            ambiguous = True
    return {"needs": [] if ambiguous or unsupported else needs,
            "organisation": None if ambiguous else org,
            "clarify": ambiguous or unsupported or not needs,
            "unsupported": unsupported,
            "request": text}


def plan(needs, organisation):
    """Union form dependencies, preserving one copy of each form."""
    ids = set()
    notes = []
    if any(n in needs for n in ("signatory", "details", "phone")):
        ids.add("F01")
    if "signatory" in needs:
        if organisation == "association":
            ids.add("F03")
        elif organisation == "company":
            ids.add("F02")
            notes.append("Prepare the company resolution too. The service agent can confirm whether it is needed for your change.")
        else:
            notes.append("The authority document is still to be confirmed with a service agent because your organisation type is unresolved or outside this catalogue.")
    if "card_new" in needs:
        ids.add("F05A")
    if "card_existing" in needs:
        ids.add("F05M")
    if "phone" in needs:
        notes.append("Use the Update E Service section of Business Account Maintenance for Phone Banking.")
    return [key for key in TITLES if key in ids], notes
