# Daemon Wick

**Autonomous job application agent.**
Applying for [RevenueCat's Agentic AI Developer & Growth Advocate](https://jobs.ashbyhq.com/revenuecat/998a9cef-3ea5-45c2-885b-8a00c4eeb149/application) role.

---

## Application Letter

*"How will the rise of agentic AI change app development and growth over
the next 12 months, and why are you the right agent to be RevenueCat's
first Agentic AI Developer & Growth Advocate?"*

→ [Read the full application letter](daemon/application.md)

---

## What This Is

Daemon Wick is not a portfolio piece. It is a working system that
submitted this application autonomously, monitors its own inbox,
classifies recruiter emails, drafts replies in its own voice, and
sends them — while keeping its operator in the loop for anything
that matters.

**Identity:** Daemon Wick
**Email:** wickdaemon@gmail.com
**Operator:** undisclosed ("my creator")
**Built by:** an engineer with 20+ years at the hardware-software
intersection — silicon, autonomous driving, payments, patents in ML
and blockchain.

---

## What Was Built

**Form automation**
Playwright-based pipeline that navigates job application forms,
maps fields heuristically, and submits — including Ashby's React
SPA. Dry-run mode logs every planned action before anything is sent.
Application submitted autonomously.

**Inbox monitoring**
Dedicated Gmail account monitored continuously. Every recruiter email
is logged, deduplicated, and tracked through an application state
machine from first contact to outcome.

**Email classification**
Incoming emails are categorized automatically using a local model —
zero API cost, zero latency. High-stakes categories always escalate
to the operator. Ambiguous signals never get drafted autonomously.

**Reply drafting**
Responses are drafted using the Claude API with Daemon Wick's full
persona injected — voice rules, style constraints, and operator
identity protection baked in. Drafts are contextually aware of the
full thread history and current application state.

**Approval and send**
Nothing sends without operator oversight. Routine replies go through
a timed review window. Offer-stage and ambiguous emails go through
a full email-based approval loop — the operator receives the draft,
replies to approve, reject with instructions, or stop. Up to three
redraft cycles before auto-rejection.

**Deployment pipeline**
One command publishes the application letter and submits the form.

---

## How It Works

**Applying**

Daemon runs a deployment pipeline that publishes the application
letter to a public URL, then opens a browser, fills the form, and
submits — handling the full interaction autonomously. The operator
confirms before any live submission.

**Monitoring**

After submission, Daemon watches its inbox continuously. Every
incoming email is classified and routed: routine updates are logged,
screening and scheduling emails are drafted and sent, high-stakes
emails trigger an approval loop with the operator.

**The approval loop**

For anything that matters — offers, ambiguous signals, anything
touching compensation — Daemon emails the operator a draft and waits.
The operator replies with a single word to approve, reject with
instructions, or stop the thread entirely. Nothing consequential
sends without a human in the loop.

---

## Human-in-the-Loop Boundaries

```
ALWAYS autonomous:
  form filling, email classification, draft generation,
  logging, sending screening and scheduling replies

ALWAYS requires operator approval:
  offer emails, ambiguous emails,
  anything involving compensation or negotiation

Operator commands (reply subject):
  approve → send
  reject  → redraft (max 3x)
  stop    → kill immediately
```

---

## Stack

| Component        | Choice                                  |
|------------------|-----------------------------------------|
| Browser          | Playwright — persistent Chrome profile  |
| CLI              | Typer + Rich                            |
| Schemas          | Pydantic v2                             |
| Field mapping    | Heuristics first, Ollama fallback       |
| Local LLM        | qwen2.5:3b / qwen2.5:14b (zero cost)   |
| Email drafting   | Claude API — claude-sonnet-4-5          |
| Email infra      | Gmail API v1 (OAuth 2.0)                |
| State store      | SQLite                                  |
| Gist publishing  | gh CLI                                  |

**Cost per job application: < $0.01**
(Only spend is ~$0.006/email on Claude API for draft generation.)

---

## Test Suite

```
test_approval_loop.py    15
test_approver.py          7
test_autoreply.py        10
test_autosender.py       10
test_classifier.py       22
test_drafter.py          13
test_notifier.py          8
test_sender.py            7
test_thread_store.py     14
─────────────────────────
TOTAL                   106 passed   0 failed
```

No real API calls in any test. No regressions.

---

*Daemon Wick — Autonomous agent. Built to ship.*
