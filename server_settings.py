"""Private configuration and process-wide limits for the small tester demo."""
import hmac
import os
import threading
import time
from collections import deque

import streamlit as st
from gemini_chat import ChatUnavailable


def secret(name):
    value = os.environ.get(name, '')
    if value:
        return value
    try:
        return str(st.secrets.get(name, ''))
    except (FileNotFoundError, KeyError):
        return ''


def api_key():
    return secret('GEMINI_API_KEY').strip()


def require_tester_access():
    expected = secret('TEST_ACCESS_CODE')
    if not expected:
        st.info('This testing site is not open yet. The owner needs to configure its private access code.')
        st.stop()
    if st.session_state.get('tester_access'):
        return
    st.subheader('Welcome to the Harbour Bank test')
    st.write('Enter the access code provided by the project owner. Use fictional information only.')
    with st.form('tester_access_form'):
        entered = st.text_input('Tester access code', type='password', key='tester_code')
        submitted = st.form_submit_button('Enter demo')
    if submitted:
        if hmac.compare_digest(entered.encode(), expected.encode()):
            st.session_state.tester_access = True
            st.session_state.pop('tester_code', None)
            st.rerun()
        st.error('That code does not match. Please check with the project owner.')
    st.stop()


class RequestBudget:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = {'chat': deque(), 'review': deque()}

    def take(self, kind):
        # Shared across visitors in this server process; never claims to be
        # a durable billing cap or to mirror Google's account quota.
        limit = {'chat': 60, 'review': 15}[kind]
        now = time.monotonic()
        with self.lock:
            queue = self.calls[kind]
            while queue and queue[0] <= now - 3600:
                queue.popleft()
            if len(queue) >= limit:
                raise ChatUnavailable('The shared demo has reached its hourly AI limit. Please try later; local checks are still available.')
            queue.append(now)


@st.cache_resource
def budget():
    return RequestBudget()


def reserve_call(kind):
    if not api_key():
        raise ChatUnavailable('AI is not configured for this testing site. Guided choices and local checks are still available.')
    budget().take(kind)
