"""Gemini-assisted intake with deterministic routing and local PDF review."""
import hashlib
from html import escape
from itertools import groupby
import io
import json
from pathlib import Path
import re
import zipfile

import streamlit as st
from form_selection import interpret, plan, TITLES
from document_review import review, CHECKER_VERSION
from review_snapshots import compact_locations, location_label, snapshot
from gemini_chat import respond, ChatUnavailable, MODEL
from review_help import explain_review
from review_questions import answer_question
from gemini_review import augment_review, policy_revision, AI_REVIEW_VERSION
from server_settings import api_key, require_tester_access, reserve_call

ROOT = Path(__file__).resolve().parent
CATALOGUE = ROOT / 'Source Catalogue'
SCENARIOS = [
    {
        'id': 'treasurer_handover',
        'title': 'New treasurer',
        'summary': 'A new treasurer needs account access and asks about a month-long handover.',
        'story': 'You are Alex Morgan, newly appointed to the board of Nordic Business Council, a registered association, as its treasurer. You need access to all of the council’s bank accounts so you can carry out your new responsibilities. Erik Larsen, the previous treasurer, has stepped down from the board and no longer holds an official role. You would like him to keep his existing account access for a month while he trains you. You are unsure whether the bank will allow this.',
        'prompt': 'Play this role in the chat below. Explain the handover you want, ask what you need to do, and let the assistant ask any follow-up questions.',
    },
    {
        'id': 'company_director_card',
        'title': 'New company director',
        'summary': 'A director needs account access and a new business debit card.',
        'story': 'You are Priya Shah, a newly appointed director of Cedar Lane Trading, a private limited company. You will help manage business expenses. You need to be added as someone who can sign on the company’s bank account, and you also want a new business debit card in your name. The existing directors should keep their account access. You do not know whether the two requests need separate paperwork.',
        'prompt': 'Play this role in the chat below. Describe both things you want in everyday language. If you do not know an account detail or signing rule, say so.',
    },
    {
        'id': 'departing_employee',
        'title': 'Departing employee',
        'summary': 'Cancel a departing employee’s card and Phone Banking access.',
        'story': 'You are Daniel Lee, a director of Willow Office Supplies, a private limited company. An employee, Maya Chen, is leaving next week. She has a business debit card and uses Phone Banking, but she is not an account signatory. You want her card cancelled and her Phone Banking access removed while keeping everyone else’s access unchanged. You are unsure which details the bank will need.',
        'prompt': 'Play this role in the chat below. Explain which services Maya uses and what should stop. Let the assistant work out the forms.',
    },
]


def instruction(text):
    st.markdown('<p class="hb-instruction">' + escape(text) + '</p>', unsafe_allow_html=True)


def applicant_text(text):
    """Hide internal catalogue/rule codes from applicant-facing explanations."""
    labels = {
        'F01': 'Business Account Maintenance form',
        'F02': 'Board Resolution form for companies',
        'F03': 'Minutes of Meeting form for associations',
        'F05A': 'Business Debit Card Application form',
        'F05M': 'Business Debit Card Maintenance form',
    }
    text = str(text or '')
    for code, label in labels.items():
        text = re.sub(r'\b' + code + r'\b', label, text)
    text = re.sub(r'\bR\d{2}(?:\s*/\s*R\d{2})*\b', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip(' .;')

st.set_page_config(page_title='Harbour Bank | Business banking', page_icon='⚓', layout='centered')
st.markdown('<style>' + (ROOT / 'ui.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)
st.markdown('<div class="hb-masthead"><div class="hb-wordmark">harbour</div>'
            '<div class="hb-service">BUSINESS BANKING</div></div>', unsafe_allow_html=True)
require_tester_access()
st.caption('Testing edition · Fictional information only · Nothing is sent to a bank.')
st.markdown('<div class="hb-hero"><div class="hb-eyebrow">LESS PAPERWORK. MORE CLARITY.</div>'
            '<h1>Your next step, made simple.</h1>'
            '<p>Find the right forms. Catch the small mistakes. Arrive ready for your bank visit.</p></div>', unsafe_allow_html=True)
with st.container(border=True):
    st.markdown('**The problem this demo tackles**')
    st.write('Business banking changes can involve several forms, and applicants may not know which ones fit their situation or notice inconsistencies before visiting a branch. Describe what you need in your own words; the assistant recommends the likely forms and checks completed PDFs for avoidable mistakes. It helps reduce paperwork back-and-forth so a Customer Service Officer can focus on verification and judgment. This prototype recommends and checks forms—it does not fill them in or approve requests.')



st.session_state['gemini_enabled'] = True
if st.button('Hide staff view' if st.session_state.get('show_staff_review') else 'Show staff view', key='staff_preview_button'):
    st.session_state.show_staff_review = not st.session_state.get('show_staff_review', False)
staff_preview = st.session_state.get('show_staff_review', False)
if staff_preview:
    st.caption('Tester preview: open the Staff review tab to see this session’s summary. This is not a staff login.')
st.caption('AI chat sends your request and handbook guidance to Google. AI document review is a separate choice when uploading forms.')
if not api_key():
    st.info('AI is not connected right now. You can still describe a straightforward request in the chat; complex or unclear requests will need a Customer Service Officer.')

# Migrate sessions from the earlier placeholder screen without losing the request.
if st.session_state.get('selector_version', 0) < 2:
    st.session_state.selector_version = 2
    st.session_state.setdefault('messages', [])
    st.session_state.setdefault('selection', None)
    st.session_state.setdefault('stage', 'request')
    st.session_state.pop('ai_options', None)
    st.session_state.pop('roleplay_scenario', None)
    if st.session_state.get('stage') in {'purpose', 'organisation'}:
        st.session_state.stage = 'request'
st.session_state.setdefault('selected_scenario', None)


def submit_request(text):
    if api_key() and st.session_state.get('gemini_enabled'):
        submit_ai_request(text)
        return
    st.session_state.messages.append({'role': 'user', 'content': text})
    previous = st.session_state.get('selection') or {}
    parsed = interpret(text)
    request = '\n'.join(m['content'] for m in st.session_state.messages if m['role'] == 'user')
    if st.session_state.stage == 'organisation' and previous.get('needs'):
        if parsed.get('organisation'):
            selection = dict(previous, organisation=parsed['organisation'], request=request)
            st.session_state.selection = selection
            st.session_state.stage = 'ready'
            return
        if text.lower().replace('’', "'").strip() in ["i don't know", 'not sure', 'unknown']:
            st.session_state.selection = dict(previous, organisation='unknown', request=request)
            st.session_state.stage = 'ready'
            return
    needs = list(dict.fromkeys(previous.get('needs', []) + parsed.get('needs', [])))
    organisation = parsed.get('organisation') or previous.get('organisation')
    selection = dict(parsed, needs=needs, organisation=organisation, request=request,
                     unsupported=previous.get('unsupported', False) or parsed.get('unsupported', False))
    st.session_state.selection = selection
    if parsed.get('clarify') and previous.get('needs') and st.session_state.stage not in {'organisation'}:
        st.session_state.stage = 'help'
        st.session_state.messages.append({'role': 'assistant', 'content': 'I may not have understood how these changes fit together, so I don’t want to guess which forms you need. A Customer Service Officer should help with this request. You can keep describing the situation here if there is another detail I should consider.'})
    elif not needs and parsed.get('clarify'):
        st.session_state.stage = 'request'
        st.session_state.messages.append({'role': 'assistant', 'content': 'I’m not sure which change you need yet. Could you describe what should change, who or what it affects, and anything else the bank should know? It’s fine to say if you’re unsure.'})
    elif 'signatory' in needs and not organisation:
        st.session_state.stage = 'organisation'
        st.session_state.messages.append({'role': 'assistant', 'content': 'What kind of organisation is this for? You can describe it in your own words or say if you’re unsure.'})
    elif selection.get('unsupported') and not needs:
        st.session_state.stage = 'help'
        st.session_state.messages.append({'role': 'assistant', 'content': 'This request is outside the services covered by this demo. A Customer Service Officer can help with it. If there is another business banking change you want to prepare, describe it here.'})
    else:
        st.session_state.stage = 'ready'


def submit_ai_request(text):
    st.session_state.messages.append({'role': 'user', 'content': text})
    previous = st.session_state.selection
    try:
        with st.spinner('Working out which forms you need…'):
            reserve_call('chat')
            state = respond(api_key(), st.session_state.messages, previous)
    except ChatUnavailable as error:
        # No automatic retries, model switching or paid fallback.
        st.session_state.gemini_status = 'Last request did not complete.'
        st.session_state.ai_notice = str(error)
        selection = dict(previous) if previous else interpret(text)
        selection['request'] = '\n'.join(m['content'] for m in st.session_state.messages if m['role'] == 'user')
        selection.pop('summary', None)
        st.session_state.selection = selection
        st.session_state.stage = 'request'
        st.session_state.messages.append({'role': 'assistant', 'content': 'I couldn’t complete the AI check just now. Please keep describing your request in your own words; if the request is complex, a Customer Service Officer will need to help.'})
        return
    st.session_state.gemini_status = 'Connected — the last chat reply came from Gemini.'
    st.session_state.pop('ai_notice', None)
    selection = {'needs': state['needs'],
                 'organisation': None if state['organisation'] == 'unasked' else state['organisation'],
                 'request': '\n'.join(m['content'] for m in st.session_state.messages if m['role'] == 'user'),
                 'unsupported': state['unsupported'], 'summary': state['summary'], 'clarify': state['clarify']}
    st.session_state.selection = selection
    if state['summary']:
        st.session_state.messages.append({'role': 'assistant', 'content': state['summary']})
    if state.get('explanation'):
        st.session_state.messages.append({'role': 'assistant', 'content': state['explanation']})
    if state['clarify']:
        st.session_state.ai_question = state['question']
        st.session_state.messages.append({'role': 'assistant', 'content': state['question']})
        st.session_state.stage = 'ai_question'
    elif not state['needs']:
        st.session_state.stage = 'help'
    elif 'signatory' in state['needs'] and selection['organisation'] is None:
        st.session_state.stage = 'organisation'
    else:
        st.session_state.stage = 'ready'


def selected_forms():
    selection = st.session_state.selection
    if not selection or st.session_state.stage != 'ready':
        return [], []
    return plan(selection['needs'], selection['organisation'])


try:
    manifest = json.loads((CATALOGUE / 'catalogue.json').read_text(encoding='utf-8-sig'))
    available = {}
    for entry in manifest['forms']:
        path = (CATALOGUE / entry['file']).resolve()
        if path.parent != CATALOGUE.resolve():
            raise ValueError('Invalid catalogue path')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('Catalogue file has changed: ' + entry['id'])
        available[entry['id']] = (entry, content)
except (OSError, ValueError, KeyError) as error:
    st.error('The form catalogue could not be verified. Please ask the project owner to check it.')
    st.caption(str(error))
    st.stop()


def render_downloads(ids, prefix):
    for form_id in ids:
        entry, content = available[form_id]
        st.download_button('Download ' + TITLES[form_id], content,
                           file_name=entry['file'], mime='application/pdf',
                           key=prefix + form_id, width='stretch')
    if len(ids) > 1:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for form_id in ids:
                entry, content = available[form_id]
                bundle.writestr(entry['file'], content)
        st.download_button('Download these forms together', buffer.getvalue(),
                           file_name='Harbour Bank selected forms.zip', mime='application/zip',
                           key=prefix + 'zip')
    if 'F01' in ids:
        instruction('If the maintenance PDF does not display in your browser, save it and open it in Adobe Acrobat Reader.')


def render_correction(item, files, widget_key):
    # Keep the correction readable while retaining a short plain-language explanation.
    title = applicant_text(item.get('title')) or 'Please check this item'
    details = applicant_text(item.get('details')) or 'Please check this entry against your request. Ask a Customer Service Officer if you are unsure.'
    st.write('**' + title.rstrip('.') + '.**')
    st.write(details + ('' if details.endswith(('.', '!', '?')) else '.'))
    locations = item.get('locations', [])
    if locations:
        st.caption(compact_locations(locations))
        for start in range(0, len(locations), 2):
            pair = locations[start:start + 2]
            for column, location in zip(st.columns(len(pair)), pair):
                with column:
                    st.caption(location_label(location))
                    source = next((data for name, data in files
                                   if name == location['document'] and hashlib.sha256(data).hexdigest() == location['sha256']), None)
                    picture = snapshot(source, location) if source is not None else None
                    if picture:
                        st.image(picture, width=384)
                    else:
                        instruction('Open this page to check the entry; a precise snapshot is unavailable.')



def render_review_questions(key, stored):
    st.write('**Questions about these corrections?**')
    st.caption('Ask for an explanation. Your question, discussion, review findings and handbook guidance go to Google for an AI answer. This does not recheck the PDFs.')
    discussion = stored.setdefault('discussion', [])
    for item in discussion:
        for role, text in [('user', item['question']), ('assistant', item['answer'])]:
            with st.chat_message(role):
                st.markdown('<div class="hb-chat-sender">' + ('You' if role == 'user' else 'Harbour assistant · ' + item.get('source', 'Local help')) + '</div>', unsafe_allow_html=True)
                st.write(text)
    with st.form(key + '_question_form', clear_on_submit=True):
        question = st.text_input('Ask about your forms', key=key + '_question',
                                 placeholder='For example, which meeting date should I change?', max_chars=2000)
        submitted = st.form_submit_button('Ask Harbour', type='primary')
    if submitted:
        if not question.strip():
            instruction('Type a question first.')
            return
        source = 'AI help'
        try:
            with st.spinner('Looking at your question…'):
                reserve_call('chat')
                answer = answer_question(api_key(), stored['report'], discussion, question)
        except ChatUnavailable as error:
            st.info(str(error) + ' Showing the existing review explanation instead.')
            source = 'Local help'
            answer = explain_review(stored['report'], question)
        discussion.append({'question': question.strip(), 'answer': answer, 'source': source})
        stored['report']['review_questions'] = list(discussion)
        st.rerun()


def render_uploads(key):
    st.write('Upload your completed PDFs below. You can add more than one.')
    instruction("Use fictional test data. Bring supporting identity and address documents for in-person verification; don't upload them here.")
    files = st.file_uploader('Completed forms', type=['pdf'], accept_multiple_files=True, key=key)
    use_ai = st.toggle('Include AI review', key=key + '_ai',
                       help='On Check my forms, send these PDFs, your request and the Word handbook to Google for additional checks.')
    if use_ai:
        instruction('Checking will send these PDFs, your request and the handbook to Google. Use fictional forms only. Review ' + AI_REVIEW_VERSION + '.')
        if not api_key():
            st.caption('AI is not configured for this site. Local checks are still available.')
    results = st.session_state.setdefault('review_results', {})
    context = json.dumps({'selection': st.session_state.selection, 'stage': st.session_state.stage, 'messages': st.session_state.messages}, sort_keys=True)
    review_mode = AI_REVIEW_VERSION + policy_revision() if use_ai else 'local'
    fingerprint = hashlib.sha256((CHECKER_VERSION + review_mode + context + ''.join(hashlib.sha256(f.getvalue()).hexdigest() + f.name for f in (files or []))).encode()).hexdigest()
    if key in results and results[key]['fingerprint'] != fingerprint:
        del results[key]
    if files:
        accepted = []
        for file in files:
            if file.getvalue()[:5] != b'%PDF-':
                st.error(file.name + ': please export this as a PDF and try again.')
                continue
            accepted.append((file.name, file.getvalue()))
            st.write(f'📄 {file.name} · {file.size / 1024:,.0f} KB')
        if accepted:
            st.success(f'{len(accepted)} file(s) received for this session.')
            if len(files) > 10:
                st.warning('Please review no more than 10 PDFs together.')
            elif len(accepted) == len(files) and st.button('Check my forms', key=key + '_review', type='primary'):
                required, _ = selected_forms()
                conversation = '\n'.join(m['role'] + ': ' + m['content'] for m in st.session_state.messages)
                with st.spinner('Checking the readable form fields…'):
                    report = review(accepted, required, conversation)
                if use_ai:
                    try:
                        with st.spinner('Gemini is checking your forms against the handbook…'):
                            reserve_call('review')
                            report = augment_review(api_key(), accepted,
                                                    report, st.session_state.selection)
                    except ChatUnavailable as error:
                        report['ai_review'] = {'status': 'unavailable', 'message': str(error)}
                results[key] = {'fingerprint': fingerprint, 'context': context, 'report': report}
            if key in results:
                report = results[key]['report']
                ai = report.get('ai_review', {})
                if ai.get('status') == 'completed':
                    st.caption('Your forms have been checked. The page snapshots point to each item.')
                    with st.chat_message('assistant'):
                        st.markdown('<div class="hb-chat-sender">Harbour assistant</div>', unsafe_allow_html=True)
                        st.write(applicant_text(ai.get('summary')) or 'I’ve checked the forms. Please review the notes and page snapshots below, then ask me if anything is unclear.')
                elif ai.get('status') == 'unavailable':
                    st.info('The AI check could not finish. The quick checks are shown below. ' + ai['message'])
                    with st.chat_message('assistant'):
                        st.markdown('<div class="hb-chat-sender">Harbour assistant</div>', unsafe_allow_html=True)
                        corrections = [f for f in report['findings'] if f['owner'] == 'applicant']
                        if corrections:
                            st.write(f'I found {len(corrections)} item(s) to check or fix. Start with the notes and page snapshots below. When you have made changes, upload the updated forms and check them again.')
                        else:
                            st.write('I did not find a clear correction in the checks I could complete. A Customer Service Officer still needs to review the forms.')
                else:
                    st.caption('Quick checks only · Turn on AI review for a more detailed check.')
                corrections = [f for f in report['findings'] if f['owner'] == 'applicant']
                if ai.get('status') not in {'completed', 'unavailable'}:
                    with st.chat_message('assistant'):
                        st.markdown('<div class="hb-chat-sender">Harbour assistant</div>', unsafe_allow_html=True)
                        if corrections:
                            st.write(f'I found {len(corrections)} item(s) to check or fix. Start with the notes and page snapshots below. When you have made changes, upload the updated forms and check them again.')
                        else:
                            st.write('I did not find a clear correction in the checks I could complete. A Customer Service Officer still needs to review the forms.')
                if corrections:
                    st.write('**Please check these items:**')
                    for index, item in enumerate(corrections[:3]):
                        render_correction(item, accepted, key + fingerprint + str(index))
                    if len(corrections) > 3:
                        with st.expander(f'Show {len(corrections) - 3} more correction(s)'):
                            for index, item in enumerate(corrections[3:], 3):
                                render_correction(item, accepted, key + fingerprint + str(index))
                else:
                    st.write('I did not find a clear change to make. This does not confirm that the forms are complete; a Customer Service Officer still needs to review them.')
                st.info('A Customer Service Officer still needs to check uncertain details. This demo does not send your request to staff or to a bank.')
                st.write('Bring the required identity and address evidence for in-person verification if adding a signatory. Do not upload it here.')
                instruction('Replace the PDFs above to check corrected copies.')
                render_review_questions(key, results[key])
    st.caption('Uploads stay in session memory; they are not saved to the project folder.')


st.subheader('Choose a testing scenario')
instruction('Pick a story to role-play, or skip this and make up your own. Your selected story stays here while you chat below.')
selected_scenario = st.session_state.get('selected_scenario')
scenario_columns = st.columns(3, gap='medium')
for column, scenario in zip(scenario_columns, SCENARIOS):
    with column:
        with st.container(border=True):
            st.markdown('**' + scenario['title'] + '**')
            st.write(scenario['summary'])
            is_selected = selected_scenario == scenario['id']
            if st.button('Selected' if is_selected else 'Choose scenario',
                         key='scenario_select_' + scenario['id'],
                         type='primary' if is_selected else 'secondary',
                         disabled=is_selected, width='stretch'):
                st.session_state.selected_scenario = scenario['id']
                st.rerun()
if selected_scenario:
    scenario = next(item for item in SCENARIOS if item['id'] == selected_scenario)
    with st.container(border=True):
        st.markdown('**Selected scenario · ' + scenario['title'] + '**')
        st.write(scenario['story'])
        instruction(scenario['prompt'])

views = st.tabs(['01  Your request', '02  Your forms'] + (['Staff review'] if staff_preview else []))
chat, downloads = views[:2]
with chat:
    if not st.session_state.messages:
        st.subheader('Tell us what you need')
        instruction('Describe the change in your own words—who it affects, what should change, and whether it applies to one or several accounts. You don’t need to know the form names. Use fictional details only.')
    for role, messages in groupby(st.session_state.messages, key=lambda message: message['role']):
        with st.chat_message(role):
            st.markdown('<div class="hb-chat-sender">' + ('You' if role == 'user' else 'Harbour assistant') + '</div>', unsafe_allow_html=True)
            for message in messages:
                st.write(message['content'])
    stage = st.session_state.stage
    selection = st.session_state.selection
    if st.session_state.get('ai_notice'):
        st.info(st.session_state.ai_notice)
    if stage == 'help':
        instruction('A Customer Service Officer can help with this request. You can describe any other business banking change here in the chat.')
    elif stage == 'ready':
        ids, notes = selected_forms()
        with st.chat_message('assistant'):
            st.markdown('<div class="hb-chat-sender">Harbour assistant</div>', unsafe_allow_html=True)
            st.write('Please prepare these forms for your request:')
            render_downloads(ids, 'chat_')
            for note in notes:
                st.write(note)
            if selection.get('unsupported'):
                st.write('These cover the changes you selected. The other part of your original request still needs help from a Customer Service Officer.')
            st.write("Once they're filled, upload them here. If you're unsure about an entry, the Customer Service Officer can help.")
    st.write('**Your turn — describe what you need**' if not st.session_state.messages else '**Continue in the chat**')
    if prompt := st.chat_input('Describe your banking request…'):
        submit_request(prompt)
        st.rerun()
    if st.session_state.messages and st.button('Start a new request', key='reset'):
        for key in ['selector_version', 'selection', 'stage', 'messages', 'chat_uploads', 'standalone_uploads', 'review_results', 'ai_notice', 'ai_question', 'ai_options', 'selected_scenario']:
            st.session_state.pop(key, None)
        st.rerun()
    st.caption('Nothing is sent to a bank. Refreshing the browser may start a new session.')
    st.divider()
    st.subheader('Upload and check your forms')
    instruction('When you have completed the recommended forms, upload them here. You can check more than one together.')
    render_uploads('chat_uploads')

with downloads:
    st.subheader('Forms for your request')
    ids, notes = selected_forms()
    if ids:
        render_downloads(ids, 'selected_')
        for note in notes:
            st.write(note)
        st.write('When the forms are ready, upload and check them below the chat.')
    else:
        st.write('Tell us about your request first. Your selected forms will appear here.')
    with st.expander('Browse all blank forms'):
        st.caption('Full catalogue — these are not all required for your request.')
        render_downloads(list(available), 'catalogue_')

if staff_preview:
    with views[2]:
        st.subheader('Prepared review summary')
        st.caption('Demo preview — no Customer Service Officer has received this and no request has been approved.')
        current_context = json.dumps({'selection': st.session_state.selection, 'stage': st.session_state.stage, 'messages': st.session_state.messages}, sort_keys=True)
        current = [(key, item['report']) for key, item in st.session_state.get('review_results', {}).items() if item['context'] == current_context]
        if not current:
            st.write('Upload completed PDFs and choose Check my forms to prepare a summary. Changed requests need a new review.')
        for key, report in current:
            st.write('**Chat upload**')
            st.info('Ready for staff review. Check the items below before approving the request.')
            ai = report.get('ai_review', {})
            if ai.get('status') == 'completed':
                st.caption('Reviewed with AI and local checks. Staff make the final decision.')
            elif ai.get('status') == 'unavailable':
                st.info('AI review did not complete. ' + ai['message'])
            else:
                st.caption('Local checks only. AI review was not run.')
            st.write('**What the applicant wants**')
            selection = st.session_state.selection or {}
            st.write(selection.get('summary') or selection.get('request') or 'Request not yet confirmed. Read the conversation below.')
            ordered_findings = sorted(report['findings'], key=lambda item: item['owner'] != 'applicant')
            staff_actions = {
                'Confirm the accounts covered': 'Check that the forms cover the accounts requested.',
                'Confirm removal and signing instructions': 'Compare who is being added or removed with the request. Check signing rules against bank records.',
                'Check certification signatures': 'Look for the required signatures on the form. An empty electronic field does not always mean a signature is missing.',
                'Confirm eligibility for account access': 'Confirm the person holds a current role that permits account access.',
                'Confirm meeting dates agree': 'Compare the meeting date with the date in the appendix.',
                'Confirm which copy is current': 'Ask which copy should be used before proceeding.',
                'Confirm the required form bundle': 'Confirm the request and check that all required forms are included.',
            }
            for owner, heading in [('applicant', 'Corrections to discuss with the applicant'), ('agent', 'Checks for the Customer Service Officer')]:
                items = [(i, item) for i, item in enumerate(ordered_findings, 1) if item['owner'] == owner]
                st.write('**' + heading + '**')
                if not items:
                    st.caption('No items identified in this group. This does not confirm the forms are complete.')
                    continue
                rows = []
                for index, item in items:
                    location = compact_locations(item.get('locations', []))
                    if not location:
                        names = [d['name'] for d in report['documents'] if d['name'] in item.get('evidence', [])]
                        location = '; '.join(names) or 'Submission as a whole'
                    rows.append({'No.': index, 'Issue': item['title'], 'Document / page': location,
                                 'What to do': staff_actions.get(item['title'], item['details'])})
                st.table(rows)
            st.write('**At the appointment**')
            st.write('For a new signatory, ask for the required certified identity and address evidence. Verify it in person; it is not part of this upload check.')
            with st.expander('Supporting-document requirements'):
                st.write(report['supporting_evidence'])
            with st.expander('Full conversation and files'):
                st.text(report['request'] or 'No request recorded.')
                for document in report['documents']:
                    st.write(document['name'] + ' — ' + (TITLES.get(document['form_id']) or 'Type not confirmed'))
            with st.expander('Review evidence and technical details'):
                if ai.get('status') == 'completed':
                    st.caption('Word handbook version: ' + ai['policy_sha256'][:12])
                    for limitation in ai.get('limitations', []):
                        st.write('AI limitation: ' + limitation)
                for index, item in enumerate(ordered_findings, 1):
                    st.write(f"**{index}. {item['title']}**")
                    st.write(item['details'])
                    st.caption('Rule: ' + item['rule'] + ' · Source: ' + item.get('source', 'Local checker'))
                    for evidence in item['evidence']:
                        st.text(evidence)
                    if item.get('related_observations'):
                        st.caption('Repeated AI observations were combined here. Original wording is retained below for staff to assess, not as additional corrections.')
                        for observation in item['related_observations']:
                            st.text(observation['title'] + ': ' + observation['details'])
                st.caption(report['coverage'])
            st.download_button('Download full review summary', json.dumps(report, indent=2, ensure_ascii=False), file_name='Harbour Bank review summary.json', mime='application/json', key=key + '_report')

st.divider()
st.caption('Fictional bank · Local prototype · Human verification and approval remain with bank staff.')
