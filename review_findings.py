"""Conservative consolidation: same issue AND shared precise evidence."""
import copy
import re


def issue_family(item):
    title = item['title'].casefold()
    if re.search(r'\be-?mail\b', title):
        return 'email'
    if 'meeting date' in title:
        return 'meeting_date'
    if 'registration' in title and any(w in title for w in ('consistent', 'match', 'reference', 'number')):
        return 'registration'
    if 'identity' in title and any(w in title for w in ('match', 'reference', 'consistent')):
        return 'identity'
    return None


def anchors(item, documents):
    result = set()
    for loc in item.get('locations', []):
        doc = next((d for d in documents if d['sha256'] == loc['sha256']), None)
        if not doc:
            continue
        page = loc['page']
        if loc.get('field'):
            result.add((doc['sha256'], page, loc['field']))
        elif loc.get('text'):
            quote = loc['text']
            result.add((doc['sha256'], page, 'quote:' + quote))
            # Match a quote to a field only when its nontrivial value is unique
            # on that page. Never merge by page, person name or rule alone.
            matches = [key for key, field in doc['fields'].items()
                       if field['page'] == page and len(field['value']) >= 5
                       and field['value'] in quote]
            if len(matches) == 1:
                result.add((doc['sha256'], page, matches[0]))
    return result


def consolidate(local, additions, documents):
    findings = copy.deepcopy(local)
    for incoming in additions:
        item = copy.deepcopy(incoming)
        cited = [d for d in documents if any(l['sha256'] == d['sha256'] for l in item.get('locations', []))]
        if cited and all(d['form_id'] == 'F03' for d in cited):
            item['title'] = re.sub(r'\b(?:board )?resolution form\b', 'Minutes of Meeting', item['title'], flags=re.I)
        family = issue_family(item)
        match = next((old for old in findings
                      if old['owner'] == item['owner']
                      and anchors(old, documents) & anchors(item, documents)
                      and ((family and family == issue_family(old))
                           or old['title'].rstrip('.').casefold() == item['title'].rstrip('.').casefold())), None)
        if match is None:
            findings.append(item)
            continue
        # Keep the local, evidence-based wording. Preserve AI claims for staff
        # without promoting an unsupported paraphrase into another correction.
        match.setdefault('related_observations', []).append(item)
        match['source'] = 'Local checker + Gemini' if match.get('source', 'Local checker').startswith('Local') else 'Gemini'
        for location in item.get('locations', []):
            if location not in match['locations']:
                match['locations'].append(location)
        for evidence in item.get('evidence', []):
            if evidence not in match['evidence']:
                match['evidence'].append(evidence)
    return findings
