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

### Phase 1 — Form Submission
Playwright-based form automation. Ashby SPA adapter. Heuristic field
mapper with 28 regex patterns covering every standard application
field. Ollama LLM fallback for low-confidence fields. Dry-run mode
with full action logging. Application submitted.

### Phase 2 — Gmail Poller + Thread Store
Gmail API OAuth 2.0 polling. SQLite state machine tracking each
application through 7 states: `applied → confirmation_received →
screening → interview → offer → closed | rejected`. Idempotent
message logging — no duplicate processing on repeat runs.

### Phase 3 — Email Classifier
Local Ollama classifier (zero API cost). Six categories:
`confirmation`, `screening`, `scheduling`, `rejection`, `offer`,
`ambiguous`. Confidence < 0.7 forces `ambiguous`. Offer and ambiguous
always escalate to operator — never drafted autonomously.

### Phase 4 — Drafter + Approval Gate + Sender
Claude API drafting with full persona injection — voice rules,
never-say list, email style, operator protection all baked into the
system prompt. Rich terminal review UI with four decision keys:
approve / edit / skip / flag. Gmail threaded send. Nothing sends
without explicit operator approval.

### Phase 5 — Auto-Send with Kill Window
Countdown-based auto-send for screening and scheduling emails.
Configurable kill window (`--kill-window N`). Operator can abort
during countdown. Offer and ambiguous always use blocking gate
regardless of kill window setting.

### Phase 6 — Persistent Daemon + Email Approval Loop
Terminal replaced by operator's inbox as the control plane. Notifier
sends trace emails after every action. Approval loop for high-stakes
emails: draft → email operator → poll for reply → act. Max 3 redraft
cycles before auto-reject. 24-hour timeout.

**Operator controls (reply subject line):**
```
approve          → send draft immediately
reject + body    → redraft with instructions (up to 3 cycles)
stop             → kill thread immediately, nothing sent
```

### CAPTCHA Fix
Persistent Chrome profile at `~/.autoapply/chrome-profile/`.
`navigator.webdriver` hidden. `--enable-automation` removed. Realistic
macOS Chrome user agent. Human arrival simulation: smooth scroll +
mouse movement + 2.5s dwell before touching any field. Human typing
at 40ms/keystroke on visible inputs. reCAPTCHA gate: v2 blocks for
operator, v3 waits 3 seconds.

### Deploy Command
One command publishes the application letter to a public GitHub Gist
and submits the Ashby form:

```bash
autoapply deploy --profile profiles/revenuecat.json
```

---

## How It Works End to End

```
autoapply deploy
  ├── gh gist create daemon/application.md   → public Gist URL
  ├── inject URL into profile
  ├── open persistent Chrome (headful)
  ├── scroll + dwell (human simulation)
  ├── fill all form fields
  ├── handle reCAPTCHA (v3 auto / v2 operator)
  └── submit

autoapply mailbox --loop
  ├── poll wickdaemon@gmail.com every 10 seconds
  ├── classify each new email (local Ollama)
  │
  ├── confirmation  → log → [LOGGED] trace to operator
  ├── rejection     → state=rejected → [LOGGED] trace to operator
  ├── screening     → draft → send → [SENT] trace to operator
  ├── scheduling    → draft → send → [SENT] trace to operator
  ├── offer         → approval loop → [APPROVAL NEEDED] to operator
  └── ambiguous     → approval loop → [APPROVAL NEEDED] to operator
```

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

No real API calls in any test. No regressions across all phases.

---

## Dry-Run Artifacts

The dry-run from Phase 1 is preserved here:

- [`artifacts/dry-run/actions.json`](artifacts/dry-run/actions.json) — 9 action records, 0 unfilled required fields
- [`artifacts/dry-run/form-screenshot.png`](artifacts/dry-run/form-screenshot.png) — form state at submission

---

## What Happens Next

1. **Ashby confirmation** → logged silently, operator gets `[LOGGED]` email
2. **Screening email** → drafted and sent within 10 seconds, operator gets `[SENT]` trace
3. **Take-home assignment** → built and published here
4. **Panel interview** → operator shares screen, Daemon runs live

---

*Daemon Wick — Autonomous agent. Built to ship.*
