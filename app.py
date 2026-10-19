"""Gemini-assisted intake with deterministic routing and local PDF review."""
import hashlib
from html import escape
from itertools import groupby
import io
import json
from pathlib import Path
import zipfile

import streamlit as st
from form_selection import interpret, plan, PURPOSES, ORGANISATIONS, TITLES
from document_review import review, CHECKER_VERSION
from review_snapshots import compact_locations, location_label, snapshot
from gemini_chat import respond, ChatUnavailable, MODEL
from review_help import explain_review
from review_questions import answer_question
from gemini_review import augment_review, policy_revision, AI_REVIEW_VERSION
from server_settings import api_key, require_tester_access, reserve_call

ROOT = Path(__file__).resolve().parent
CATALOGUE = ROOT / 'Source Catalogue'
ROLEPLAY_SCENARIOS = [
    ('treasurer', '1 · New treasurer — keep the previous treasurer during handover',
     'You are Alex Morgan, newly appointed to the board of Nordic Business Council, a registered association, as its treasurer. You need access to all of the council’s bank accounts so you can carry out your new responsibilities. Erik Larsen, the previous treasurer, has stepped down from the board and no longer holds an official role. You would like him to keep his existing account access for a month while he trains you. You are unsure whether the bank will allow this.',
     'Explain the handover you want, ask what you need to do, and let the assistant ask any follow-up questions.'),
    ('director', '2 · New company director — account access and a debit card',
     'You are Priya Shah, a newly appointed director of Cedar Lane Trading, a private limited company. You will help manage business expenses. You need to be added as someone who can sign on the company’s bank account, and you also want a new business debit card in your name. The existing directors should keep their account access. You do not know whether the two requests need separate paperwork.',
     'Describe both things you want in everyday language. If you do not know an account detail or signing rule, say so.'),
    ('employee', '3 · Departing employee — debit card and Phone Banking',
     'You are Daniel Lee, a director of Willow Office Supplies, a private limited company. An employee, Maya Chen, is leaving next week. She has a business debit card and uses Phone Banking, but she is not an account signatory. You want her card cancelled and her Phone Banking access removed while keeping everyone else’s access unchanged. You are unsure which details the bank will need.',
     'Explain which services Maya uses and what should stop. Let the assistant work out the forms.'),
]


def instruction(text):
    st.markdown('<p class="hb-instruction">' + escape(text) + '</p>', unsafe_allow_html=True)

st.set_page_config(page_title='Harbour Bank | Business banking', page_icon='⚓', layout='centered')
st.markdown('<style>' + (ROOT / 'ui.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)
st.markdown('<div class="hb-masthead"><div class="hb-wordmark">harbour</div>'
            '<div class="hb-service">BUSINESS BANKING</div></div>', unsafe_allow_html=True)
require_tester_access()
st.caption('Testing edition · Fictional information only · Nothing is sent to a bank.')
st.markdown('<div class="hb-hero"><div class="hb-eyebrow">LESS PAPERWORK. MORE CLARITY.</div>'
            '<h1>Your next step, made simple.</h1>'
            '<p>Find the right forms. Catch the small mistakes. Arrive ready for your bank visit.</p></div>', unsafe_allow_html=True)



st.session_state['gemini_enabled'] = True
if st.button('Hide staff view' if st.session_state.get('show_staff_review') else 'Show staff view', key='staff_preview_button'):
    st.session_state.show_staff_review = not st.session_state.get('show_staff_review', False)
staff_preview = st.session_state.get('show_staff_review', False)
if staff_preview:
    st.caption('Tester preview: open the Staff review tab to see this session’s summary. This is not a staff login.')
st.caption('AI chat sends your request and handbook guidance to Google. AI document review is a separate choice when uploading forms.')
if not api_key():
    st.info('AI is not configured yet. You can still use guided choices and local form checks.')

# Migrate sessions from the earlier placeholder screen without losing the request.
if 'selector_version' not in st.session_state:
    old = st.session_state.get('messages', [])
    requests = [m['content'] for m in old if m['role'] == 'user']
    st.session_state.selector_version = 1
    st.session_state.messages = []
    st.session_state.selection = None
    st.session_state.stage = 'request'
    if requests:
        text = requests[-1]
        selection = interpret(text)
        st.session_state.selection = selection
        st.session_state.messages = [{'role': 'user', 'content': text}]
        st.session_state.stage = 'purpose' if selection['clarify'] else ('organisation' if 'signatory' in selection['needs'] and selection['organisation'] is None else 'ready')


def submit_request(text):
    if api_key() and st.session_state.get('gemini_enabled'):
        submit_ai_request(text)
        return
    selection = interpret(text)
    st.session_state.selection = selection
    st.session_state.messages.append({'role': 'user', 'content': text})
    st.session_state.stage = 'purpose' if selection['clarify'] else ('organisation' if 'signatory' in selection['needs'] and selection['organisation'] is None else 'ready')


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
        selection['organisation'] = None
        selection.pop('summary', None)
        st.session_state.selection = selection
        st.session_state.stage = 'purpose'
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
        st.session_state.ai_options = list(dict.fromkeys(state['options'] + ["I don't know"]))
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
    # One instruction, one compact location line. Detailed rules stay with staff.
    st.write('**' + item['title'] + '.**')
    if item.get('details'):
        st.write(item['details'])
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
                        st.write(ai.get('summary') or 'I’ve checked the forms. Please review the corrections and snapshots below, then ask me if anything is unclear.')
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


views = st.tabs(['01  Your request', '02  Your forms', '03  Check forms'] + (['Staff review'] if staff_preview else []))
chat, downloads, uploads = views[:3]
with chat:
    # Keep the selected role-play brief visible throughout the conversation so
    # testers can refer back to their instructions while answering follow-ups.
    active_scenario = st.session_state.get('roleplay_scenario')
    if active_scenario:
        scenario = next(item for item in ROLEPLAY_SCENARIOS if item[0] == active_scenario)
        with st.container(border=True):
            st.caption('ROLE-PLAY GUIDE · AVAILABLE THROUGHOUT THIS CHAT')
            st.write('**' + scenario[1] + '**')
            st.write(scenario[2])
            instruction(scenario[3])
            if st.button('Change scenario', key='change_roleplay_scenario'):
                st.session_state.pop('roleplay_scenario', None)
                st.rerun()
    else:
        st.write('**Role-play scenarios**')
        instruction('Choose a story to keep its role-play instructions visible while you chat. Then describe the request in your own words. You can still invent your own fictional situation.')
        for scenario_id, title, story, prompt in ROLEPLAY_SCENARIOS:
            with st.expander(title, expanded=(not st.session_state.messages and scenario_id == 'treasurer')):
                st.write(story)
                instruction(prompt)
                if st.button('Use this scenario', key='use_scenario_' + scenario_id):
                    st.session_state.roleplay_scenario = scenario_id
                    st.rerun()
    if not st.session_state.messages:
        st.subheader('What can we help you with?')
        instruction('Use the role-play guide above, then tell us what you need as if you were the applicant.')
    for role, messages in groupby(st.session_state.messages, key=lambda message: message['role']):
        with st.chat_message(role):
            st.markdown('<div class="hb-chat-sender">' + ('You' if role == 'user' else 'Harbour assistant') + '</div>', unsafe_allow_html=True)
            for message in messages:
                st.write(message['content'])
    stage = st.session_state.stage
    selection = st.session_state.selection
    if st.session_state.get('ai_notice'):
        st.info(st.session_state.ai_notice)
    if stage == 'request':
        if st.session_state.get('roleplay_scenario'):
            instruction('You do not need to know form names. All names and organisations are fictional.')
    elif stage == 'ai_question':
        for index, option in enumerate(st.session_state.ai_options):
            if st.button(option, key=f'ai_option_{len(st.session_state.messages)}_{index}', width='stretch'):
                submit_request(option)
                st.rerun()
        if st.button('Use guided choices instead', key='ai_guided'):
            st.session_state.stage = 'purpose'
            st.rerun()
    elif stage == 'purpose':
        with st.chat_message('assistant'):
            st.markdown('<div class="hb-chat-sender">Harbour assistant</div>', unsafe_allow_html=True)
            if selection['unsupported']:
                st.write('Part of your request is outside this demo. A Customer Service Officer would need to help with that part.')
            st.write('Which changes should we prepare forms for? You can choose more than one.')
            with st.form('purpose_form'):
                needs = st.multiselect('Choose your changes', options=list(PURPOSES), format_func=PURPOSES.get)
                if st.form_submit_button('Continue', type='primary'):
                    if needs:
                        selection['needs'] = needs
                        st.session_state.messages.append({'role': 'assistant', 'content': 'Which changes should we prepare forms for?'})
                        st.session_state.messages.append({'role': 'user', 'content': '; '.join(PURPOSES[n] for n in needs)})
                        st.session_state.stage = 'organisation' if 'signatory' in needs and selection['organisation'] is None else 'ready'
                        st.rerun()
                    else:
                        st.warning("Choose a change, or use ‘I don't know’ below.")
            if st.button("I don't know / my request isn't listed", key='unknown_purpose'):
                st.session_state.stage = 'help'
                st.rerun()
    elif stage == 'organisation':
        with st.chat_message('assistant'):
            st.markdown('<div class="hb-chat-sender">Harbour assistant</div>', unsafe_allow_html=True)
            st.write('What type of organisation is this for?')
            for value, label in ORGANISATIONS.items():
                if st.button(label, key='org_' + value, width='stretch'):
                    selection['organisation'] = value
                    st.session_state.messages.append({'role': 'assistant', 'content': 'What type of organisation is this for?'})
                    st.session_state.messages.append({'role': 'user', 'content': label})
                    st.session_state.stage = 'ready'
                    st.rerun()
    elif stage == 'help':
        st.info("A Customer Service Officer would need to help choose the forms for this request. This demo can't send the case to a Customer Service Officer yet. You can still upload any forms you already have in ‘Upload forms’.")
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
        with st.expander('Upload completed forms', expanded=True):
            render_uploads('chat_uploads')
        if st.button('Change my selections', key='edit_selection'):
            st.session_state.stage = 'purpose'
            selection['organisation'] = None
            st.rerun()
    # Buttons handle incremental answers; typed follow-ups are conservatively clarified.
    st.write('**Your turn — type your request below**' if stage == 'request' else '**Continue your request**')
    if stage == 'request':
        instruction('Tell us who you are, what has changed and what you would like the bank to do. Write naturally, then press Enter or the send arrow.')
    if prompt := st.chat_input('Describe your banking request…'):
        if api_key() and st.session_state.get('gemini_enabled'):
            submit_ai_request(prompt)
        elif stage == 'organisation':
            answer = interpret(prompt)
            if answer['organisation']:
                selection['organisation'] = answer['organisation']
                st.session_state.messages.append({'role': 'user', 'content': prompt})
                # New service wording must not be silently ignored.
                if answer['needs']:
                    selection['needs'] = list(dict.fromkeys(selection['needs'] + answer['needs']))
                st.session_state.stage = 'ready'
            elif prompt.lower().replace('’', "'").strip() in ["i don't know", 'not sure', 'unknown']:
                selection['organisation'] = 'unknown'
                st.session_state.messages.append({'role': 'user', 'content': prompt})
                st.session_state.stage = 'ready'
            else:
                st.session_state.messages.append({'role': 'user', 'content': prompt})
        elif stage in ['ready', 'help', 'purpose']:
            # Never silently interpret 'also' / corrections as replacing earlier intent.
            st.session_state.messages.append({'role': 'user', 'content': prompt})
            selection['request'] += '\n' + prompt
            selection['organisation'] = interpret(prompt)['organisation']
            st.session_state.stage = 'purpose'
        else:
            submit_request(prompt)
        st.rerun()
    if st.session_state.messages and st.button('Start a new request', key='reset'):
        for key in ['selector_version', 'selection', 'stage', 'messages', 'chat_uploads', 'standalone_uploads', 'review_results', 'ai_notice', 'ai_question', 'ai_options']:
            st.session_state.pop(key, None)
        st.rerun()
    st.caption('Nothing is sent to a bank. Refreshing the browser may start a new session.')

with downloads:
    st.subheader('Forms for your request')
    ids, notes = selected_forms()
    if ids:
        render_downloads(ids, 'selected_')
        for note in notes:
            st.write(note)
        st.write('Ready? Open Upload forms, or use the upload box in your chat.')
    else:
        st.write('Tell us about your request first. Your selected forms will appear here.')
    with st.expander('Browse all blank forms'):
        st.caption('Full catalogue — these are not all required for your request.')
        render_downloads(list(available), 'catalogue_')

with uploads:
    st.subheader('Upload your completed forms')
    render_uploads('standalone_uploads')


if staff_preview:
    with views[3]:
        st.subheader('Prepared review summary')
        st.caption('Demo preview — no Customer Service Officer has received this and no request has been approved.')
        current_context = json.dumps({'selection': st.session_state.selection, 'stage': st.session_state.stage, 'messages': st.session_state.messages}, sort_keys=True)
        current = [(key, item['report']) for key, item in st.session_state.get('review_results', {}).items() if item['context'] == current_context]
        if not current:
            st.write('Upload completed PDFs and choose Check my forms to prepare a summary. Changed requests need a new review.')
        for key, report in current:
            st.write('**' + ('Chat upload' if key == 'chat_uploads' else 'Upload forms tab') + '**')
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
