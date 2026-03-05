import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

from .schemas import Profile, FieldInventory
from . import agent
from .adapters.ashby import wait_for_form, extract_fields

app = typer.Typer(help="Autonomous job application agent.")


@app.command()
def apply(
    url: str,
    profile: Annotated[str, typer.Option("--profile")] = "profiles/default.json",
    submit: Annotated[bool, typer.Option("--submit")] = False,
    headless: Annotated[bool, typer.Option("--headless")] = True,
    no_llm: Annotated[bool, typer.Option("--no-llm")] = False,
) -> None:
    """Fill and optionally submit a job application form."""
    with open(profile) as f:
        prof = Profile(**json.load(f))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts_dir = f"runs/{timestamp}_apply"
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    if submit and headless:
        rprint("[yellow][WARNING] Running headless with --submit. CAPTCHA solving will require "
               "operator input. Consider omitting --headless for live submission.[/yellow]")

    dry_run = not submit
    result = asyncio.run(agent.run(
        url=url,
        profile=prof,
        dry_run=dry_run,
        headless=headless,
        artifacts_dir=artifacts_dir,
        use_llm=not no_llm,
    ))

    # Print actions table
    actions_path = os.path.join(artifacts_dir, "actions.json")
    if os.path.exists(actions_path):
        with open(actions_path) as f:
            actions = json.load(f)
        table = Table(title="Action Records")
        table.add_column("Seq", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Selector")
        table.add_column("Value")
        table.add_column("Step")
        table.add_column("Note")
        for a in actions:
            table.add_row(
                str(a["seq"]), a["type"], a["selector"],
                a["value"][:60], str(a["step"]), a["note"],
            )
        rprint(table)

    # Save result
    result_path = os.path.join(artifacts_dir, "result.json")
    with open(result_path, "w") as f:
        json.dump(result.model_dump(), f, indent=2)

    if result.error:
        rprint(f"[red]ERROR: {result.error}[/red]")
        raise typer.Exit(code=1)

    if dry_run:
        rprint("[yellow]DRY RUN COMPLETE — no form submitted[/yellow]")
    else:
        rprint("[green]SUBMITTED[/green]")
        # Copy artifacts with friendlier names for submit
        if submit:
            submit_dir = "runs/revenuecat_submit"
            Path(submit_dir).mkdir(parents=True, exist_ok=True)
            import shutil
            final_png = os.path.join(artifacts_dir, "final.png")
            if os.path.exists(final_png):
                shutil.copy(final_png, os.path.join(submit_dir, "confirmation.png"))
            shutil.copy(result_path, os.path.join(submit_dir, "result.json"))


@app.command()
def inspect(url: str) -> None:
    """Recon only — extract and print field inventory, no filling."""
    async def _inspect():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle")
            await wait_for_form(page)
            inventory = await extract_fields(page)
            await browser.close()
            return inventory

    inventory = asyncio.run(_inspect())

    # Print as Rich JSON panel
    inv_json = inventory.model_dump_json(indent=2)
    rprint(Panel(JSON(inv_json), title="Field Inventory"))

    # Save to file
    # Extract company name from Ashby URL pattern: jobs.ashbyhq.com/{company}/...
    parts = url.rstrip("/").split("/")
    slug = "form"
    for i, p in enumerate(parts):
        if "ashbyhq.com" in p and i + 1 < len(parts):
            slug = parts[i + 1]
            break
    slug = re.sub(r"[^a-zA-Z0-9]", "_", slug)[:30]
    out_dir = f"runs/{slug}_inspect"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = os.path.join(out_dir, "field_inventory.json")
    with open(out_path, "w") as f:
        f.write(inv_json)
    rprint(f"Saved to {out_path}")


@app.command()
def init_profile() -> None:
    """Write a starter profile.json to stdout."""
    starter = {
        "identity": {
            "name": "Your Name",
            "email": "you@example.com",
            "phone": None,
            "location": None,
            "linkedin": None,
            "github": None,
            "website": None,
        },
        "files": {
            "resume": None,
            "cover_letter": None,
        },
        "answers": {
            "why_company": None,
            "why_role": None,
            "work_authorization": None,
            "start_date": None,
            "salary": None,
            "application_url": None,
        },
        "eeo": {
            "auto_fill": False,
        },
    }
    print(json.dumps(starter, indent=2))


@app.command()
def deploy(
    url: str,
    profile: Annotated[str, typer.Option("--profile")] = "profiles/default.json",
    headless: Annotated[bool, typer.Option("--headless")] = False,
    no_llm: Annotated[bool, typer.Option("--no-llm")] = False,
    skip_gist: Annotated[bool, typer.Option("--skip-gist")] = False,
) -> None:
    """Publish application gist, fill form, and submit — full deployment."""
    from daemon.publisher import publish_gist, get_existing_gist_url

    with open(profile) as f:
        prof = Profile(**json.load(f))

    console = Console()

    # Step 1: Publish gist (or reuse existing)
    gist_url = None
    if not skip_gist:
        rprint("[cyan]Step 1: Publishing application gist...[/cyan]")
        existing = get_existing_gist_url()
        if existing:
            rprint(f"[yellow]Existing gist found: {existing}[/yellow]")
            rprint("Use this gist? [Y/n] ", end="")
            answer = input().strip().lower()
            if answer in ("", "y", "yes"):
                gist_url = existing
        if not gist_url:
            try:
                gist_url = publish_gist()
                rprint(f"[green]Gist published: {gist_url}[/green]")
            except Exception as e:
                rprint(f"[red]Gist publish failed: {e}[/red]")
                rprint("[yellow]Continuing without gist update...[/yellow]")
    else:
        rprint("[dim]Step 1: Skipped gist publishing (--skip-gist)[/dim]")

    # Inject gist URL into profile if available
    if gist_url and prof.answers.application_url != gist_url:
        rprint(f"[cyan]Updating application_url → {gist_url}[/cyan]")
        prof.answers.application_url = gist_url

    # Step 2: Fill and submit the form
    rprint("[cyan]Step 2: Filling and submitting form...[/cyan]")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts_dir = f"runs/{timestamp}_deploy"
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    result = asyncio.run(agent.run(
        url=url,
        profile=prof,
        dry_run=False,
        headless=headless,
        artifacts_dir=artifacts_dir,
        use_llm=not no_llm,
    ))

    # Print actions table
    actions_path = os.path.join(artifacts_dir, "actions.json")
    if os.path.exists(actions_path):
        with open(actions_path) as f:
            actions = json.load(f)
        table = Table(title="Deploy Action Records")
        table.add_column("Seq", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Selector")
        table.add_column("Value")
        table.add_column("Step")
        table.add_column("Note")
        for a in actions:
            table.add_row(
                str(a["seq"]), a["type"], a["selector"],
                a["value"][:60], str(a["step"]), a["note"],
            )
        console.print(table)

    # Save result
    result_path = os.path.join(artifacts_dir, "result.json")
    with open(result_path, "w") as f:
        json.dump(result.model_dump(), f, indent=2)

    if result.error:
        rprint(f"[red]DEPLOY ERROR: {result.error}[/red]")
        raise typer.Exit(code=1)

    if result.submitted:
        rprint("[green]DEPLOYED — form submitted successfully.[/green]")
        if gist_url:
            rprint(f"[green]Application gist: {gist_url}[/green]")
        rprint(f"[dim]Artifacts: {artifacts_dir}[/dim]")
    else:
        rprint("[yellow]Form was not submitted. Check artifacts for details.[/yellow]")


def _infer_company_from_message(message) -> str:
    """Extract company slug from sender domain for state updates."""
    try:
        domain = message.from_addr.split("@")[1].split(">")[0]
        return domain.split(".")[0].lower()
    except (IndexError, AttributeError):
        return message.from_addr.lower()


def _is_test_subject(subject: str) -> bool:
    """Return True if subject contains 'test' (case-insensitive)."""
    return "test" in subject.lower()


def _send_test_acknowledgement(
    message,
    store,
    company: str,
) -> str:
    """
    Send an immediate test acknowledgement reply.
    Bypasses classifier, drafter, and approval loop.
    Returns the sent Gmail message ID.
    """
    from daemon.mailbox.sender import send
    from daemon.mailbox.notifier import notify
    from daemon.mailbox.classifier import (
        ClassificationResult, Category
    )

    test_reply = "Received your test. Pipeline is live.\n\nDaemon Wick"

    sent_id = send(
        message, test_reply, store, company, edits_made=False
    )

    # Synthetic result for notification trace
    synthetic_result = ClassificationResult(
        category=Category.confirmation,
        confidence=1.0,
        raw_response="test-subject-bypass",
        model="none",
        fallback=False,
    )
    notify(message, synthetic_result, test_reply, company)

    return sent_id


@app.command()
def mailbox(
    watch: Annotated[bool, typer.Option("--watch")] = False,
    status: Annotated[bool, typer.Option("--status")] = False,
    classify_inbox: Annotated[bool, typer.Option("--classify")] = False,
    respond: Annotated[bool, typer.Option("--respond")] = False,
    auto: Annotated[bool, typer.Option("--auto")] = False,
    loop: Annotated[bool, typer.Option("--loop")] = False,
    kill_window: Annotated[int, typer.Option("--kill-window")] = 60,
    poll_interval: Annotated[int, typer.Option("--poll-interval")] = 10,
    model: Annotated[str, typer.Option("--model")] = "qwen2.5:3b",
) -> None:
    """Watch Gmail inbox, show status, classify, or draft+send replies."""
    from daemon.mailbox.poller import GmailPoller
    from daemon.mailbox.thread_store import ThreadStore

    if auto and not respond:
        rprint(
            "[yellow]--auto has no effect without --respond[/yellow]"
        )
        raise typer.Exit(1)

    if loop and respond:
        rprint(
            "[yellow]--loop and --respond are mutually exclusive. "
            "Use --loop for persistent daemon mode.[/yellow]"
        )
        raise typer.Exit(1)

    if poll_interval < 1:
        rprint("[yellow]--poll-interval must be at least 1 second[/yellow]")
        raise typer.Exit(1)

    if watch:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        poller = GmailPoller()
        poller.watch()

    elif status:
        store = ThreadStore()
        apps = store.list_applications()
        if not apps:
            rprint("[yellow]No applications tracked yet.[/yellow]")
            return
        table = Table(title="Application States")
        table.add_column("Company", style="cyan")
        table.add_column("State", style="green")
        table.add_column("Updated", style="dim")
        for a in apps:
            table.add_row(a["company"], a["state"], a["updated_at"])
        console = Console()
        console.print(table)

    elif classify_inbox:
        from daemon.mailbox.classifier import classify, DEFAULT_ACTIONS

        store = ThreadStore()
        poller = GmailPoller(store=store)

        table = Table(title="Inbox Classification")
        table.add_column("From", style="cyan", max_width=30)
        table.add_column("Subject", style="white", max_width=40)
        table.add_column("Category", style="green")
        table.add_column("Confidence", style="yellow")
        table.add_column("Action", style="dim")
        table.add_column("Fallback", style="red")

        count = 0
        for message in poller.fetch_new_messages():
            result = classify(message, model=model)
            action = DEFAULT_ACTIONS[result.category]

            # State machine updates based on classification
            if result.category.value == "rejection":
                store.set_state(
                    _infer_company_from_message(message), "rejected"
                )
            elif result.category.value == "screening":
                store.set_state(
                    _infer_company_from_message(message), "screening"
                )

            table.add_row(
                message.from_addr[:30],
                message.subject[:40],
                result.category.value,
                f"{result.confidence:.0%}",
                action,
                "yes" if result.fallback else "",
            )
            count += 1

        console = Console()
        if count == 0:
            rprint("[yellow]No new messages to classify.[/yellow]")
        else:
            console.print(table)
            rprint(f"\n[green]{count} message(s) classified.[/green]")
            rprint(
                "[yellow]Offer and ambiguous results require operator "
                "attention before any response is drafted.[/yellow]"
            )

    elif respond:
        from daemon.mailbox.classifier import (
            classify, Category, DEFAULT_ACTIONS
        )
        from daemon.mailbox.drafter import draft as draft_reply
        from daemon.mailbox.approver import review, ApprovalDecision
        from daemon.mailbox.sender import send

        store = ThreadStore()
        poller = GmailPoller(store=store)

        approved_count = 0
        skipped_count = 0
        flagged_count = 0
        sent_count = 0
        aborted_count = 0

        for message in poller.fetch_new_messages():
            company = _infer_company_from_message(message)
            result = classify(message, model=model)
            action = DEFAULT_ACTIONS[result.category]

            rprint(f"\n[cyan]From:[/cyan] {message.from_addr}")
            rprint(f"[cyan]Subject:[/cyan] {message.subject}")
            rprint(
                f"[cyan]Classification:[/cyan] "
                f"{result.category.value} "
                f"({result.confidence:.0%} confidence)"
            )

            # Escalate immediately — never draft for offer or ambiguous
            if action == "escalate":
                rprint(
                    f"[red]ESCALATE — {result.category.value} email "
                    f"requires operator attention. No draft generated.[/red]"
                )
                flagged_count += 1
                continue

            # Log-only categories — no response needed
            if action in ("log", "log_rejected"):
                if action == "log_rejected":
                    store.set_state(company, "rejected")
                    rprint("[yellow]Rejection logged. State → rejected.[/yellow]")
                else:
                    rprint("[dim]Confirmation logged. No response needed.[/dim]")
                continue

            # queue_draft — screening and scheduling get a draft
            if action == "queue_draft":
                if result.category.value == "screening":
                    store.set_state(company, "screening")

                rprint("[green]Drafting reply...[/green]")
                try:
                    draft_text = draft_reply(message, store, company)
                except ValueError as e:
                    rprint(f"[red]Draft failed: {e}[/red]")
                    flagged_count += 1
                    continue
                except Exception as e:
                    rprint(f"[red]Draft error: {e}[/red]")
                    flagged_count += 1
                    continue

                thread_history = store.get_messages(company)

                if auto:
                    # Auto-send path: countdown with kill window
                    from daemon.mailbox.autosender import (
                        review_and_send, AutoSendDecision
                    )
                    auto_result = review_and_send(
                        message, draft_text, kill_window=kill_window
                    )
                    if auto_result.decision == AutoSendDecision.sent:
                        try:
                            sent_id = send(
                                message,
                                auto_result.draft,
                                store,
                                company,
                                edits_made=False,
                            )
                            sent_count += 1
                            rprint(f"[green]SENT. Message ID: {sent_id}[/green]")
                        except PermissionError as e:
                            rprint(f"[red]{e}[/red]")
                            flagged_count += 1
                        except Exception as e:
                            rprint(f"[red]Send failed: {e}[/red]")
                            flagged_count += 1
                    else:
                        # Aborted via Ctrl+C
                        aborted_count += 1

                else:
                    # Manual path: existing approval gate (unchanged)
                    approval = review(message, draft_text, thread_history)

                    if approval.decision == ApprovalDecision.approved:
                        try:
                            sent_id = send(
                                message,
                                approval.final_draft,
                                store,
                                company,
                                edits_made=approval.edits_made,
                            )
                            sent_count += 1
                            approved_count += 1
                            rprint(f"[green]SENT. Message ID: {sent_id}[/green]")
                        except PermissionError as e:
                            rprint(f"[red]{e}[/red]")
                            rprint(
                                "[red]Re-run OAuth flow with gmail.send scope "
                                "then retry.[/red]"
                            )
                            flagged_count += 1
                        except Exception as e:
                            rprint(f"[red]Send failed: {e}[/red]")
                            flagged_count += 1

                    elif approval.decision == ApprovalDecision.skipped:
                        skipped_count += 1

                    elif approval.decision == ApprovalDecision.flagged:
                        flagged_count += 1

        # Summary
        rprint(f"\n[bold]Session complete.[/bold]")
        rprint(f"  Sent:    {sent_count}")
        if auto:
            rprint(f"  Aborted: {aborted_count}")
        else:
            rprint(f"  Skipped: {skipped_count}")
        rprint(f"  Flagged: {flagged_count}")

        if flagged_count > 0:
            rprint(
                "[yellow]Flagged items require manual review. "
                "Check runs/threads.db for thread state.[/yellow]"
            )

    elif loop:
        import time as _time
        from daemon.mailbox.thread_store import ThreadStore
        from daemon.mailbox.poller import GmailPoller
        from daemon.mailbox.classifier import (
            classify, Category, DEFAULT_ACTIONS
        )
        from daemon.mailbox.drafter import draft as draft_reply
        from daemon.mailbox.sender import send
        from daemon.mailbox.notifier import notify
        from daemon.mailbox.approval_loop import (
            run as run_approval_loop,
            ApprovalLoopDecision,
        )

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        store = ThreadStore()
        poller = GmailPoller(store=store)

        rprint("[green]Daemon Wick loop started.[/green]")
        rprint(
            f"[dim]Polling every {poll_interval}s. "
            "Ctrl+C to stop.[/dim]"
        )

        while True:
            rprint(f"\n[dim]Polling...[/dim]")

            try:
                for message in poller.fetch_new_messages():
                    company = _infer_company_from_message(message)

                    # Test subject bypass — before any classification
                    if _is_test_subject(message.subject):
                        rprint(
                            f"[yellow]TEST subject detected: "
                            f"'{message.subject}' — auto-acknowledging[/yellow]"
                        )
                        try:
                            sent_id = _send_test_acknowledgement(
                                message, store, company
                            )
                            rprint(
                                f"[green]Test acknowledgement sent. "
                                f"id={sent_id}[/green]"
                            )
                        except Exception as e:
                            rprint(f"[red]Test acknowledgement failed: {e}[/red]")
                        continue   # skip rest of pipeline for this message

                    cls_result = classify(message, model=model)
                    action = DEFAULT_ACTIONS[cls_result.category]

                    rprint(
                        f"[cyan]{message.from_addr}[/cyan] — "
                        f"{cls_result.category.value} "
                        f"({cls_result.confidence:.0%})"
                    )

                    if action == "escalate":
                        # Offer or ambiguous — email approval loop
                        rprint(
                            f"[red]ESCALATE: {cls_result.category.value} "
                            f"— starting approval loop[/red]"
                        )
                        loop_result = run_approval_loop(
                            message, cls_result, store, company
                        )
                        if loop_result.decision == ApprovalLoopDecision.sent:
                            notify(
                                message, cls_result,
                                loop_result.final_draft, company
                            )
                            rprint(
                                f"[green]Approval loop: sent after "
                                f"{loop_result.cycles_used} cycle(s).[/green]"
                            )
                        else:
                            notify(
                                message, cls_result,
                                None, company
                            )
                            rprint(
                                f"[yellow]Approval loop: "
                                f"{loop_result.decision.value} after "
                                f"{loop_result.cycles_used} cycle(s).[/yellow]"
                            )

                    elif action in ("log", "log_rejected"):
                        # No reply needed — just notify
                        if action == "log_rejected":
                            store.set_state(company, "rejected")
                        notify(message, cls_result, None, company)
                        rprint("[dim]Logged. Operator notified.[/dim]")

                    elif action == "queue_draft":
                        # Immediate send
                        if cls_result.category.value == "screening":
                            store.set_state(company, "screening")
                        try:
                            draft_text = draft_reply(
                                message, store, company
                            )
                            sent_id = send(
                                message, draft_text, store, company
                            )
                            notify(
                                message, cls_result, draft_text, company
                            )
                            rprint(
                                f"[green]Sent immediately. "
                                f"id={sent_id}[/green]"
                            )
                        except Exception as e:
                            rprint(f"[red]Send error: {e}[/red]")
                            notify(message, cls_result, None, company)

            except KeyboardInterrupt:
                rprint("\n[yellow]Loop stopped by operator.[/yellow]")
                break
            except Exception as e:
                rprint(f"[red]Poll error: {e}[/red]")
                logging.error(f"Poll cycle error: {e}", exc_info=True)

            rprint(f"[dim]Sleeping {poll_interval}s...[/dim]")
            try:
                _time.sleep(poll_interval)
            except KeyboardInterrupt:
                rprint("\n[yellow]Loop stopped by operator.[/yellow]")
                break

    else:
        rprint(
            "[yellow]Specify --watch, --status, --classify, "
            "--respond, or --loop[/yellow]"
        )
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
