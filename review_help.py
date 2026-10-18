"""Local explanations from existing review findings. No network requests."""
import re
from review_snapshots import compact_locations


def explain_review(report, question):
    question = question.strip()
    if re.search(r'\b(fixed|corrected|replaced|updated|recheck)\b', question, re.I):
        return 'To check a corrected copy, replace the PDFs above and choose Check my forms again. Asking here does not change the forms or repeat the review.'
    if re.search(r'\b(change my request|actually|instead|now want)\b', question, re.I):
        return 'Please describe the change in Your request, then choose Check my forms again. The current review still reflects your earlier request.'
    terms = set(re.findall(r'[a-z0-9@]+', question.casefold())) - {
        'what', 'which', 'where', 'how', 'why', 'can', 'could', 'would', 'should',
        'the', 'a', 'an', 'i', 'my', 'me', 'you', 'is', 'it', 'this', 'that',
        'do', 'to', 'in', 'on', 'of', 'and', 'please', 'explain', 'correction', 'form', 'forms'}
    ranked = []
    for item in report['findings']:
        words = set(re.findall(r'[a-z0-9@]+', (item['title'] + ' ' + item['details']).casefold()))
        score = len(words & terms)
        if score:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    if not ranked:
        return 'Which item would you like explained? Mention a word from the correction, such as email, meeting date, identity reference or account access. I can show the explanation already recorded in this review.'
    selected = [item for score, item in ranked if score == ranked[0][0]][:2]
    parts = []
    for item in selected:
        location = compact_locations(item.get('locations', []))
        parts.append(item['title'] + (' — ' + location if location else '') + '\n\n' + item['details'])
    return '\n\n'.join(parts)
