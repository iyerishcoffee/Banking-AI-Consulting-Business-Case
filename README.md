# Harbour Bank — sharing and testing edition

This is a separate copy of the local prototype. The original project and its running app on port 8501 are unchanged. Changes to either copy do not automatically update the other.

## What changed

Correction snapshots now appear automatically beneath visible findings; there is no Show me toggle. Additional corrections remain expandable to keep the initial review short. Precise snapshots still require a verified field/text location. The request screen includes three fictional role-play scenarios: treasurer handover, a new director needing signatory access and a card, and a departing employee with card and Phone Banking access. These are guidance for testers, not automatically submitted requests or expected answers.

- Visitors do not enter or see the Gemini API key. It is read privately by the server.
- A tester access code is required before the application is displayed.
- A Show staff view button lets authorised testers preview the summary for their own browser session, including the technical report download. This is a role-play preview, not separate staff authentication. Do not use real customer data.
- AI chat is enabled automatically when the private server key is configured. PDF AI review remains a separate opt-in, with disclosure before sending files to Google.
- One server process permits at most 60 chat calls and 15 document-review calls per rolling hour across all visitors. Rejected calls count conservatively; there are no automatic retries.
- Only the application, five blank catalogue PDFs and Word handbook were copied. No existing credentials, uploaded submissions, tester answer keys, logs or virtual environment were copied.

## Configure privately

Copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` in THIS folder. Fill in:

```toml
GEMINI_API_KEY = "your key"
TEST_ACCESS_CODE = "a long, hard-to-guess tester access code"
```

The real file is excluded by `.gitignore`. Never put it in a repository, send it to testers, or paste it into chat. It is a plain-text server configuration file, not encrypted storage; keep it private on your computer. Environment variables with the same names are also supported. For hosting, enter these values in the host's private secrets settings instead of uploading the local file.

Restart this edition after changing secrets. Share only the tester code, never the Gemini key. A shared code is suitable for a small fictional-data pilot, not individual accounts or production authentication. Existing signed-in sessions remain authorised until the server/session restarts.

## Run locally

Double-click `Start sharing test.cmd`. It uses the original project's Python environment and opens this edition at http://localhost:8502. The original remains at http://localhost:8501. The launcher binds to your computer only; it does not publish a site or expose your network.

For a standalone environment, install `requirements.txt` and run:

```text
python -m streamlit run app.py --server.port=8502 --server.address=127.0.0.1
```

## Before sharing a public link

This edition has NOT been deployed. A hosting service is still needed; localhost links do not work on other people's computers. Publish only this folder as the app root, keeping `.streamlit/secrets.toml` excluded, and configure secrets privately on the host. Use `app.py` as the entry point. Confirm catalogue downloads and PDF review work in the hosting environment before inviting testers.

Everyone uses the owner's Gemini quota. The app does not enable billing or know the provider's billing tier. These hourly limits are an additional demo throttle, not a spending guarantee: they reset on restart and are not shared across multiple server processes or replicas. Keep the project on the intended provider plan and monitor usage. Public deployment needs separate review of hosting access controls and abuse protection.

## Test checklist

1. Confirm the access-code gate appears and rejects an incorrect code.
2. Enter the tester code. Confirm there is no API-key box or AI-chat switch. Show staff view should reveal a fourth tab for the current session; Hide staff view should remove it.
3. Describe an association signatory change. Confirm maintenance and minutes are recommended.
4. Upload fictional completed forms and enable AI review explicitly. Confirm corrections and snapshots appear.
5. Open a second browser session and confirm it starts without the first visitor's request or uploads.

Use fictional information only. Uploaded forms and conversation stay in Streamlit session memory on the host; AI-enabled actions send the described content to Google. Nothing is submitted to a bank. Review can miss errors or produce mistaken findings; Customer Service Officers retain verification and approval responsibility.

The Word handbook remains the policy master for this copy. Production staff features would require proper separate authentication. The tester staff-view button is only for previewing the current fictional session.
