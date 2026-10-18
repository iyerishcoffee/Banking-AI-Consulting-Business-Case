"""Evidence-based checks for the current editable council PDF templates.

Only field names/layouts are mapped here, never expected applicant values.
Flattened/scanned and other templates require agent review in this increment.
"""
import hashlib
import io
import re
from pypdf import PdfReader

CHECKER_VERSION = '0.2'


def inspect_pdf(name, data):
    result = {'name': name, 'sha256': hashlib.sha256(data).hexdigest(),
              'form_id': None, 'fields': {}, 'pages': [], 'limitations': []}
    if len(data) > 20 * 1024 * 1024:
        result['limitations'].append('File exceeds the 20 MB review limit.')
        return result
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            result['limitations'].append('Encrypted PDF: provide an unlocked copy or ask an agent.')
            return result
        if len(reader.pages) > 30:
            result['limitations'].append('More than 30 pages: agent review is needed.')
            return result
        acro = reader.trailer['/Root'].get('/AcroForm')
        if acro and '/XFA' in acro.get_object():
            result['limitations'].append('Dynamic XFA PDF: export a readable PDF; automated field review is unavailable.')
            return result
        canonical = reader.get_fields() or {}
        for number, page in enumerate(reader.pages, 1):
            result['pages'].append(page.extract_text() or '')
            for reference in page.get('/Annots', []):
                widget = reference.get_object()
                if widget.get('/Subtype') != '/Widget':
                    continue
                field = widget
                if '/T' not in field and '/Parent' in field:
                    field = field['/Parent'].get_object()
                key = str(field.get('/T', ''))
                if not key:
                    continue
                value = str(field.get('/V', '')).strip()
                if key in canonical and str(canonical[key].get('/V', '')).strip() != value:
                    result['limitations'].append(f'Conflicting stored values for field {key}, page {number}; agent must verify.')
                result['fields'][key] = {'value': value, 'page': number}
        text = '\n'.join(result['pages']).lower()
        if 'business account maintenance' in text:
            result['form_id'] = 'F01'
        elif 'certified copy of resolution' in text and 'the “association”' in text:
            result['form_id'] = 'F03'
        elif 'business debit card application' in text:
            result['form_id'] = 'F05A'
        elif 'business debit card maintenance' in text:
            result['form_id'] = 'F05M'
        elif 'board resolution' in text and ('private limited' in text or 'public company' in text):
            result['form_id'] = 'F02'
        if not any(len(t.strip()) > 100 for t in result['pages']):
            result['limitations'].append('No reliable page text. Scans/photos require human review in this version.')
        supported = {'F01': ['maint_1', 'maint_11', 'maint_17', 'maint_18', 'maint_33'],
                     'F03': ['association', 'meeting_day', 'meeting_month', 'meeting_year', 'person0_name']}
        required = supported.get(result['form_id'])
        if not required or not all(key in result['fields'] for key in required):
            result['limitations'].append('This field layout is not supported by the first checker; full agent review is needed.')
        result['supported'] = bool(required) and all(key in result['fields'] for key in required)
    except Exception as error:
        result['limitations'].append('PDF could not be read (' + type(error).__name__ + '). Please export it again or ask an agent.')
    return result


def review(files, required_forms, request):
    documents = [inspect_pdf(name, data) for name, data in files]
    findings = []
    evidence_locations = {}

    def add(title, details, rule, evidence, owner='applicant'):
        findings.append({'title': title, 'details': details, 'rule': rule,
                         'evidence': evidence, 'owner': owner,
                         'locations': [evidence_locations[e] for e in evidence if e in evidence_locations]})

    def evidence(doc, key):
        field = doc['fields'][key]
        label = f"{doc['name']}, page {field['page']}, {key}: {field['value'] or '[blank]'}"
        evidence_locations[label] = {'document': doc['name'], 'sha256': doc['sha256'],
                                     'page': field['page'], 'field': key}
        return label

    observed = {d['form_id'] for d in documents if d['form_id']}
    unidentified = any(d['form_id'] is None for d in documents)
    for form in required_forms:
        if form not in observed:
            from form_selection import TITLES
            add(('Confirm the ' if unidentified else 'Upload the ') + TITLES[form],
                'An unreadable/unidentified upload may contain this form; an agent must identify it.' if unidentified else 'This required form was not identified in the uploaded package.',
                'R02/R24', [], 'agent' if unidentified else 'applicant')
    for form in observed:
        matches = [d['name'] for d in documents if d['form_id'] == form]
        if len(matches) > 1:
            add('Confirm which copy is current', 'Multiple copies of the same form were uploaded.', 'R03', matches, 'agent')
    for doc in documents:
        for limitation in doc['limitations']:
            add('Part of this file needs human review', limitation, 'Extraction limitation', [doc['name']], 'agent')

    registrations = []
    identities = {}
    for doc in documents:
        if not doc.get('supported') or doc['limitations']:
            continue
        fields = doc['fields']
        def value(key):
            return fields.get(key, {}).get('value', '')
        if doc['form_id'] == 'F01':
            for key in ['maint_1', 'maint_11']:
                if value(key):
                    registrations.append((value(key), evidence(doc, key)))
            email = value('maint_33')
            if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                add('Add the missing @ to the email address' if '@' not in email else 'Correct the email address', 'Enter a complete email address, including @ and the domain.', 'R19/R21', [evidence(doc, 'maint_33')])
            elif not email:
                add('Confirm whether contact details are required', 'The email field is blank. Verify the selected action before requiring contact details.', 'R19/R21', [evidence(doc, 'maint_33')], 'agent')
            if value('maint_17') and value('maint_18'):
                identities.setdefault(value('maint_17').casefold(), []).append((value('maint_18'), evidence(doc, 'maint_18')))
            if 'maint_12' in fields:
                add('Confirm the accounts covered', 'Compare the account scope below with the applicant request. The app has not interpreted account-scope changes or bank records.', 'R08', [evidence(doc, 'maint_12')], 'agent')
            add('Confirm removal and signing instructions', 'Compare the latest applicant request with selected Add/Remove actions and the existing bank mandate. Checkbox marks and changes to prior instructions are not reliably interpreted in this version.', 'R07/R08/R22', [doc['name']], 'agent')
        if doc['form_id'] == 'F03':
            missing = [k for k in ['meeting_day', 'meeting_month', 'meeting_year'] if not value(k)]
            if missing:
                add('Complete the meeting date', 'Fill the missing meeting-date entries and make sure the date agrees with the appendix.', 'R03/R19', [evidence(doc, k) for k in missing])
            elif value('appendix_date'):
                # Date consistency is left to staff until locale/date parsing is supported.
                add('Confirm meeting dates agree', 'Compare the meeting date with the appendix date.', 'R03/R19', [evidence(doc, k) for k in ['meeting_day', 'meeting_month', 'meeting_year', 'appendix_date']], 'agent')
            blank_signatures = [key for key in ['cert_sig1', 'cert_sig2', 'appendix_cert_signature1', 'appendix_cert_signature2'] if key in fields and not value(key)]
            if blank_signatures:
                add('Check certification signatures', 'These stored signature fields are empty. Confirm visually whether a signature was inserted separately; if absent, obtain the required certification. Ink is not required for this demo.', 'R03/R06', [evidence(doc, k) for k in blank_signatures], 'agent')
            for key in fields:
                if re.fullmatch(r'person\d+_name', key) and value(key):
                    base = key.removesuffix('_name')
                    id_key = base + '_id'
                    if value(id_key):
                        identities.setdefault(value(key).casefold(), []).append((value(id_key), evidence(doc, id_key)))
                    role_key = base + '_role'
                    if re.search(r'\b(former|ex[- ]|retired)', value(role_key).lower()):
                        add('Confirm eligibility for account access', value(key) + ' is listed with a former role. The agent must verify their current designation and requested account/signatory access; do not cancel unrelated services.', 'R07/R20', [evidence(doc, key), evidence(doc, role_key)], 'agent')
            for page, text in enumerate(doc['pages'], 1):
                for match in re.finditer(r'Association registration reference:\s*([^\n]+)', text):
                    label = f"{doc['name']}, page {page}: {match.group(0)}"
                    evidence_locations[label] = {'document': doc['name'], 'sha256': doc['sha256'],
                                                 'page': page, 'text': match.group(0)}
                    registrations.append((match.group(1).strip(), label))
    if len({v for v, _ in registrations}) > 1:
        add('Make the registration reference consistent', 'Different registration references appear in the forms. Confirm the correct reference and use it consistently.', 'R09', [e for _, e in registrations])
    for person, records in identities.items():
        if len({v for v, _ in records}) > 1:
            add('Match the identity reference for ' + person.title(), 'The same person has different identity references across the forms. Confirm and correct them; no identity-document upload is needed here.', 'R09/R38', [e for _, e in records])
    if not required_forms:
        add('Confirm the required form bundle', 'The intake has not selected a complete form bundle. Missing-form checks could not be completed.', 'R02/R24', [], 'agent')
    return {'status': 'Partial automated review — human verification required',
            'checker_version': CHECKER_VERSION, 'policy_basis': 'Implemented subset of Word handbook v0.9; not a full policy/RAG evaluation',
            'request': request, 'required_forms': required_forms, 'findings': findings,
            'documents': [{'name': d['name'], 'sha256': d['sha256'], 'form_id': d['form_id'], 'limitations': d['limitations']} for d in documents],
            'supporting_evidence': 'For new signatories, bring the applicable certified identity and residential-address proof for in-person verification. Where a telco bill or bank statement is used, it must be dated within three months of the update-form date. Bank staff, a corporate secretary or a notary public may certify as specified in the maintenance checklist. Staff must confirm applicability, authority, signatures and authenticity.',
            'coverage': 'Checks supported editable council forms only. No OCR, signature authentication, general semantic review or bank-record verification. Uploaded content never changes the rules. No test answer key is loaded.'}
