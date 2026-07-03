# NovaCRM Onboarding Automation

An end-to-end agentic onboarding system that automates customer onboarding for NovaCRM's CS team — from deal notification email to Rocketlane project creation and Slack channel setup.

---

## What it does

When a deal closes, an AE sends a deal notification email to the CS inbox. This system:

1. **Detects the email** — polls Gmail every 10 seconds for `New Deal` emails
2. **Parses and validates** — extracts 5 required fields via regex (no LLM cost here)
3. **Confirms the plan tier** — places an outbound AI voice call to the AE via Dialnexa
4. **Retries if no answer** — waits and calls again; falls back to a HTML email with action buttons if both fail
5. **Creates a Rocketlane project** — picks Enterprise (30-day) or Growth (14-day) template based on confirmed tier
6. **Sets up Slack** — creates a personalised channel with a tier-appropriate welcome message

Every decision is logged to `agent_log.jsonl` with timestamp, inputs, and decision rationale.

---

## Architecture

```
novacrm-onboarding/
├── intake_agent/           # Agent 1 — Intake & Routing
│   ├── poller.py           # Gmail IMAP watcher (runs continuously)
│   ├── email_parser.py     # Regex-based field extraction + validation
│   ├── ae_directory.py     # AE email → phone number lookup
│   ├── dialnexa_client.py  # Triggers outbound AI voice call
│   ├── webhook_receiver.py # FastAPI server — receives Dialnexa call results
│   ├── call_store.py       # SQLite shared state between poller + webhook receiver
│   ├── email_notifier.py   # SMTP email alerts (bounceback, fallback, escalation)
│   ├── agent_logger.py     # Structured JSON audit logging
│   └── tests/
│       └── test_email_parser.py
│
├── communication_agent/    # Agent 2 — Communication
│   └── slack_agent.py      # Creates Slack channel, sets topic, posts welcome message
│
├── rocketlane/             # Rocketlane API client
│   └── rocketlane_client.py
│
└── voice-test/             # Early exploration: Pipecat + Sarvam voice stack
    └── ...                 # Superseded by Dialnexa — kept for reference
```

**Two processes run simultaneously:**
- `poller.py` — watches Gmail and triggers Dialnexa calls
- `webhook_receiver.py` (uvicorn) — receives call results and creates Rocketlane + Slack

They share state via a SQLite database (`~/novacrm_call_store.db`).

---

## Prerequisites

- Python 3.11+
- ngrok (to expose the webhook receiver publicly)
- Accounts and API keys for: OpenAI, Dialnexa, Rocketlane, Slack
- Gmail account with IMAP enabled and an app password

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/vasupradharamac/novacrm-onboarding.git
cd novacrm-onboarding/intake_agent
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in the following:

```env
# OpenAI — transcript tier extraction
OPENAI_API_KEY=sk-...

# Gmail — the CS inbox to watch
GMAIL_ADDRESS=your-cs-inbox@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # from myaccount.google.com/apppasswords

# Email filter
DEAL_EMAIL_SUBJECT_FILTER=New Deal
POLL_INTERVAL_SECONDS=10

# Dialnexa — outbound AI voice calls
DIALNEXA_API_KEY=
DIALNEXA_AGENT_ID=                        # from Dialnexa dashboard > Agents > URL

# AE phone number for testing (you play the AE)
TEST_AE_PHONE_OVERRIDE=+91XXXXXXXXXX

# Rocketlane
ROCKETLANE_API_KEY=
ROCKETLANE_ENTERPRISE_TEMPLATE_ID=        # from template URL in Rocketlane
ROCKETLANE_GROWTH_TEMPLATE_ID=
ROCKETLANE_OWNER_EMAIL=your-email@domain.com

# Slack
SLACK_BOT_TOKEN=xoxb-...

# CS Manager — receives escalation emails
CS_MANAGER_EMAIL=priya@novacrm.com

# Retry delay: 10 for testing, 3600 (1 hour) for production
RETRY_DELAY_SECONDS=10

# Shared SQLite call store path (must be the same for both processes)
CALL_STORE_PATH=/Users/yourname/novacrm_call_store.db

# Your ngrok URL — update every time ngrok restarts
WEBHOOK_BASE_URL=https://your-ngrok-url.ngrok-free.app
```

### 3. Set up Gmail IMAP

In Gmail (the CS inbox account):
- Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP → Save
- Generate an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (requires 2FA)

### 4. Set up Dialnexa agent

1. Sign up at [dialnexa.com](https://dialnexa.com)
2. Create a new agent with this prompt:

**Welcome message:**
```
Hi, this is Nova, NovaCRM's onboarding assistant. Quick question about the {{customer_name}} deal you just closed — is this account on the Enterprise plan or the Growth plan?
```

**Global prompt:**
```
You are Nova, an automated onboarding assistant for NovaCRM. You are calling an Account Executive to confirm the plan tier for a newly closed customer account.

Your only goal: confirm whether the customer is on the Enterprise plan or the Growth plan.

Rules:
- You have already asked the question in your welcome message. Listen to their answer.
- If they clearly say "Enterprise" or "Growth", thank them briefly and end the call.
- If their answer is vague, ask exactly once: "Just to confirm — Enterprise or Growth?"
- If still unclear, say you'll flag it for the team and end the call.
- Never guess the plan tier.
- Keep all responses short and conversational.
- Do not use bullet points, markdown, or special characters.
```

3. Copy the agent ID from the URL and add to `.env` as `DIALNEXA_AGENT_ID`
4. Go to API Keys → create a key → add as `DIALNEXA_API_KEY`
5. Go to Webhooks → add your ngrok URL as `https://your-ngrok-url/call-result`

### 5. Set up Rocketlane

1. Log in to your Rocketlane account
2. Settings → Templates → New project template
3. Create **Enterprise Onboarding (30-day)** with 4 phases: Kickoff, Data Migration, Configuration, Go-Live
4. Create **Growth Onboarding (14-day)** — same phases, compressed timeline
5. Open each template, copy the numeric ID from the URL → add to `.env`
6. Settings → API → Create API key → add as `ROCKETLANE_API_KEY`

**Overdue task automations (UI only — no code):**
Settings → Automations → New Automation:
- Rule 1: When task becomes overdue → Notify Project Manager
- Rule 2: When task becomes overdue → Notify Project Owner

**Data verification automation (UI only):**
Settings → Automations → New Automation:
- Trigger: When status is changed to Completed
- Condition: Task name is exactly `Data verification sign-off`
- Action: Request for approval → Project Owner

### 6. Set up Slack

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. OAuth & Permissions → Bot Token Scopes → add:
   - `channels:manage`
   - `channels:read`
   - `chat:write`
   - `chat:write.public`
   - `groups:write`
3. Install to Workspace → copy the Bot User OAuth Token → add as `SLACK_BOT_TOKEN`

---

## Running the system

You need **three terminal tabs**:

**Terminal 1 — Webhook receiver:**
```bash
cd novacrm-onboarding/intake_agent
uvicorn webhook_receiver:app --port 8000 --reload
```

**Terminal 2 — ngrok (exposes webhook publicly):**
```bash
ngrok http 8000
```
Copy the `https://...ngrok-free.app` URL → update `WEBHOOK_BASE_URL` in `.env` and in Dialnexa's webhook settings.

**Terminal 3 — Gmail poller:**
```bash
cd novacrm-onboarding/intake_agent
python poller.py
```

The system is now live. Send a deal notification email to trigger the full pipeline.

---

## Triggering the pipeline

**Happy path email** — send from any account TO the Gmail inbox being watched:

```
Subject: New Deal - Google LLC

Hi team,

Just closed a deal with Google LLC!

Customer: Google LLC
Customer Contact: sarah.chen@google.com
Salesforce Opportunity: https://novacrm.lightning.force.com/lightning/r/Opportunity/0061234567GOOGLE/view

Thanks,
Jordan Lee
Account Executive, NovaCRM
jordan.lee@novacrm.com
```

**Malformed email** (missing contact email — triggers validation guardrail):

```
Subject: New Deal - Pentagon

Hi team,

Just closed a deal with Pentagon!

Thanks,
Jordan
```

---

## Testing without live calls

To test the webhook flow without spending Dialnexa credits, seed the call store and use curl:

```bash
# Seed test data
cd novacrm-onboarding/intake_agent
python call_store.py --seed

# Happy path — Enterprise
curl -X POST http://localhost:8000/call-result \
  -H "Content-Type: application/json" \
  -d '{"event_type":"call_ended","payload":{"call":{"id":"call_test_enterprise_001","status":"completed","transcript":"[{\"role\":\"assistant\",\"content\":\"Is this account on the Enterprise plan or the Growth plan?\"},{\"role\":\"user\",\"content\":\"It is on the Enterprise plan.\"},{\"role\":\"assistant\",\"content\":\"Perfect, got it. Thanks so much.\"}]"}}}'

# Happy path — Growth
curl -X POST http://localhost:8000/call-result \
  -H "Content-Type: application/json" \
  -d '{"event_type":"call_ended","payload":{"call":{"id":"call_test_growth_001","status":"completed","transcript":"[{\"role\":\"assistant\",\"content\":\"Is this account on the Enterprise plan or the Growth plan?\"},{\"role\":\"user\",\"content\":\"It is on the Growth plan.\"},{\"role\":\"assistant\",\"content\":\"Perfect, got it. Thanks so much.\"}]"}}}'

# Retry flow — first no-answer (triggers 10s retry)
curl -X POST http://localhost:8000/call-result \
  -H "Content-Type: application/json" \
  -d '{"event_type":"call_ended","payload":{"call":{"id":"call_test_enterprise_001","status":"no_answer"}}}'

# After retry fires, copy new call_id from terminal, then:
curl -X POST http://localhost:8000/call-result \
  -H "Content-Type: application/json" \
  -d '{"event_type":"call_ended","payload":{"call":{"id":"NEW_CALL_ID_FROM_TERMINAL","status":"no_answer"}}}'
# → Fallback email with Enterprise/Growth buttons lands in CS Manager inbox
```

---

## Running tests

```bash
cd novacrm-onboarding/intake_agent
pytest tests/ -v
```

10 tests covering:
- Happy path email parsing
- Missing field validation (all required fields)
- AE name fallback to email prefix
- Contact email fallback detection
- Plan tier never extracted from email

All tests run with zero API calls — the email parser is regex-based, no mocks needed.

---

## Audit trail

Every pipeline event is appended to `agent_log.jsonl` in the `intake_agent/` directory:

```json
{"timestamp": "2026-07-03T08:00:00Z", "event": "email_received", "sender": "jordan@novacrm.com", "subject": "New Deal - Google LLC"}
{"timestamp": "2026-07-03T08:00:02Z", "event": "email_parsed", "customer": "Google LLC", "ae": "Jordan Lee"}
{"timestamp": "2026-07-03T08:00:03Z", "event": "call_triggered", "call_id": "call_mr3xyz", "customer": "Google LLC"}
{"timestamp": "2026-07-03T08:00:35Z", "event": "plan_tier_extracted", "plan_tier": "enterprise", "decision_rationale": "Extracted 'enterprise' from call transcript using GPT-4o."}
{"timestamp": "2026-07-03T08:00:37Z", "event": "rocketlane_project_created", "project_id": 5000000116477}
{"timestamp": "2026-07-03T08:00:39Z", "event": "slack_channel_created", "channel_name": "onboarding-google-llc-enterprise"}
```

---

## Key design decisions

**Why not LangChain or LangGraph?**
The system uses a deterministic pipeline with exactly one LLM call (transcript classification). LangGraph adds orchestration complexity that isn't justified for two agents with a fixed handoff and one decision point. The explicit pipeline is simpler to debug and audit.

**Why regex for email parsing instead of LLM?**
The AE email format is structured and consistent. Regex is instant, free, and fully deterministic — there's no ambiguity for an LLM to resolve here. The LLM enters only where natural language understanding is actually needed: the voice call transcript.

**Why SQLite instead of Redis?**
SQLite is built into Python and zero-config. The call store only needs to bridge two local processes with low write volume — Redis would be the production upgrade, not the prototype choice.

**Why Dialnexa instead of Pipecat + Plivo + Sarvam?**
Dialnexa is a fully managed voice AI platform handling STT, TTS, conversation, and telephony in one service. The Pipecat stack (in `voice-test/`) was explored first and abandoned — the managed platform significantly reduces integration complexity for a project of this scope.

---

## Production upgrade path

| Current (demo) | Production |
|---|---|
| Gmail IMAP polling every 10s | Gmail API push notifications via Pub/Sub |
| SQLite call store | Redis |
| ngrok | Deployed FastAPI on Railway/Fly.io |
| Local `.env` | Secrets manager (AWS SSM, etc.) |
| RETRY_DELAY_SECONDS=10 | RETRY_DELAY_SECONDS=3600 (1 hour) |
| AE phone lookup table | Salesforce User records API |

---

## Environment variables reference

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | GPT-4o for transcript classification |
| `GMAIL_ADDRESS` | CS inbox email address |
| `GMAIL_APP_PASSWORD` | 16-char app password (not your real password) |
| `DEAL_EMAIL_SUBJECT_FILTER` | Subject filter — default: `New Deal` |
| `POLL_INTERVAL_SECONDS` | How often to check inbox — default: `10` |
| `DIALNEXA_API_KEY` | Dialnexa API key |
| `DIALNEXA_AGENT_ID` | Your Nova agent ID (from URL) |
| `TEST_AE_PHONE_OVERRIDE` | Your number for testing (plays the AE) |
| `ROCKETLANE_API_KEY` | Rocketlane API key |
| `ROCKETLANE_ENTERPRISE_TEMPLATE_ID` | Numeric template ID from Rocketlane URL |
| `ROCKETLANE_GROWTH_TEMPLATE_ID` | Numeric template ID from Rocketlane URL |
| `ROCKETLANE_OWNER_EMAIL` | Email of the project owner in Rocketlane |
| `SLACK_BOT_TOKEN` | Bot OAuth token starting with `xoxb-` |
| `CS_MANAGER_EMAIL` | Receives escalation and fallback emails |
| `RETRY_DELAY_SECONDS` | Delay before retry call — `10` test, `3600` prod |
| `CALL_STORE_PATH` | Absolute path to SQLite DB — must match across both processes |
| `WEBHOOK_BASE_URL` | Your ngrok URL — update every session |

---

## Built by

Vasupradha R — Backend & AI Engineer  
[vasupradha-portfolio.vercel.app](https://vasupradha-portfolio.vercel.app) · [github.com/vasupradharamac](https://github.com/vasupradharamac)
