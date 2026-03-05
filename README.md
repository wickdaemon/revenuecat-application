# Daemon Wick

Autonomous job application agent. Fills Ashby job forms, monitors Gmail for recruiter responses, classifies emails, drafts replies, and sends them with configurable levels of human oversight.

Built by my creator. Operated by Daemon Wick.

## Setup

### Prerequisites

- Python 3.11+
- [Playwright](https://playwright.dev/python/) (for form filling)
- [Ollama](https://ollama.com/) running locally with `qwen2.5:3b` (for email classification)
- Gmail OAuth credentials (`credentials.json` from Google Cloud Console)
- Anthropic API key (for Claude-powered draft generation)

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e autoapply/
pip install pytest
playwright install chromium
```

### Environment

Create a `.env` file in the repo root (already in `.gitignore`):

```
ANTHROPIC_API_KEY=sk-ant-...
```

Gmail OAuth tokens are generated on first run and stored in `token.json` (also gitignored).

## Commands

### `autoapply apply`

Fill and optionally submit an Ashby job application form.

```bash
# Dry run (no submission)
autoapply apply "https://jobs.ashbyhq.com/company/job-id" --profile profiles/revenuecat.json

# Live submission
autoapply apply "https://jobs.ashbyhq.com/company/job-id" --profile profiles/revenuecat.json --submit

# Non-headless (visible browser)
autoapply apply "https://jobs.ashbyhq.com/company/job-id" --headless false
```

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | `profiles/default.json` | Path to applicant profile JSON |
| `--submit` | `false` | Actually submit the form |
| `--headless` | `true` | Run browser in headless mode |
| `--no-llm` | `false` | Skip LLM-based field mapping |

### `autoapply inspect`

Extract and print the field inventory from an Ashby form without filling anything.

```bash
autoapply inspect "https://jobs.ashbyhq.com/company/job-id"
```

### `autoapply init-profile`

Print a starter profile JSON to stdout.

```bash
autoapply init-profile > profiles/mycompany.json
```

### `autoapply mailbox`

Gmail inbox management with multiple operational modes.

```bash
autoapply mailbox --help
```

| Flag | Default | Description |
|------|---------|-------------|
| `--watch` | `false` | Poll inbox and print new messages |
| `--status` | `false` | Show application state table |
| `--classify` | `false` | Classify inbox messages using Ollama |
| `--respond` | `false` | Classify, draft, and send with terminal approval |
| `--auto` | `false` | With `--respond`: auto-send after kill window |
| `--loop` | `false` | Persistent daemon mode (email-based control) |
| `--kill-window` | `60` | Seconds before auto-send (with `--auto`) |
| `--poll-interval` | `10` | Seconds between inbox polls (with `--loop`) |
| `--model` | `qwen2.5:3b` | Ollama model for classification |

## Operational Modes

### Terminal Approval (`--respond`)

Interactive mode. Each email is classified, a draft is generated, and you approve/edit/skip/flag in the terminal.

```bash
autoapply mailbox --respond
```

### Auto-Send with Kill Window (`--respond --auto`)

Drafts are displayed with a countdown. Press Ctrl+C to abort before the timer expires.

```bash
autoapply mailbox --respond --auto --kill-window 30
```

### Persistent Daemon (`--loop`)

The primary operational mode. Runs continuously, polling Gmail every `--poll-interval` seconds. Boris's inbox is the control plane.

```bash
autoapply mailbox --loop
autoapply mailbox --loop --poll-interval 30
```

#### What happens per email classification:

| Classification | Action |
|---------------|--------|
| confirmation | Logged. `[LOGGED]` notification to operator. |
| rejection | State set to rejected. `[LOGGED]` notification to operator. |
| screening | Draft generated, sent immediately. `[SENT]` notification to operator. |
| scheduling | Draft generated, sent immediately. `[SENT]` notification to operator. |
| offer | Draft generated, `[APPROVAL NEEDED]` email to operator. Approval loop starts. |
| ambiguous | Same as offer. |

#### Test subject bypass

Any email with "test" in the subject (case-insensitive) gets an immediate auto-reply: `"Received your test. Pipeline is live."` This bypasses the classifier, drafter, and approval loop entirely.

```bash
# Send a test email to wickdaemon@gmail.com with subject "test"
# → immediate reply confirming the pipeline is live
```

## Email Approval Loop

For offer and ambiguous emails, the daemon sends an `[APPROVAL NEEDED]` email to the operator with the proposed draft. The operator controls the loop by replying:

| Reply Subject | Effect |
|--------------|--------|
| `approve` | Sends the draft immediately |
| `reject` | Reads reply body for instructions, redrafts with Claude, re-sends for approval |
| `stop` | Kills the thread immediately, nothing is sent |

- Maximum 3 redraft cycles. If all 3 are rejected, the operator is notified and the draft is not sent.
- If no reply within 24 hours, the loop times out and the draft is not sent.
- A `reject` with an empty body also kills the loop (fallback for API/CLI email tools).
- `stop` is checked before `approve` and `reject` — when in doubt, nothing is sent.

## Project Structure

```
autoapply/
  cli.py              CLI entry point (Typer)
  agent.py            Orchestration loop
  runner.py           Playwright action executor
  mapper.py           Heuristic field matcher
  schemas.py          Pydantic v2 models
  adapters/
    ashby.py          Ashby SPA form adapter
  backends/
    ollama.py         Ollama LLM backend

daemon/
  persona.json        Daemon Wick identity and voice rules
  application.md      Application letter
  mailbox/
    poller.py         Gmail API inbox poller
    thread_store.py   SQLite application state store
    classifier.py     Ollama email classifier
    drafter.py        Claude API reply drafter
    approver.py       Terminal approval gate
    sender.py         Gmail API sender
    autosender.py     Kill window countdown (--auto)
    notifier.py       Operator notification emails
    approval_loop.py  Email-based approve/reject/stop loop

profiles/
  revenuecat.json     Example applicant profile

runs/                 Artifacts directory (gitignored)
```

## Tests

```bash
source .venv/bin/activate
python -m pytest daemon/ -v
```

106 tests covering classifier, drafter, approver, sender, autosender, notifier, approval loop, thread store, and auto-reply. All mocked — no real API calls.
