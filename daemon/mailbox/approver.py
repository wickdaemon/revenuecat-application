import subprocess
import tempfile
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule

from .poller import EmailMessage

logger = logging.getLogger(__name__)
console = Console()


class ApprovalDecision(str, Enum):
    approved  = "approved"   # [a] — pass to sender
    edited    = "edited"     # [e] — edited then approved
    skipped   = "skipped"    # [s] — discard, no send
    flagged   = "flagged"    # [f] — mark for operator attention


@dataclass
class ApprovalResult:
    decision: ApprovalDecision
    final_draft: Optional[str]   # the text that was approved, or None
    original_draft: str          # the draft as generated
    edits_made: bool             # True if [e] path was taken


def _display_thread(message: EmailMessage, thread_history: list[dict]) -> None:
    """Render the incoming message and thread history to the terminal."""
    console.print(Rule("[bold cyan]INCOMING EMAIL[/bold cyan]"))
    console.print(f"[cyan]From:[/cyan]    {message.from_addr}")
    console.print(f"[cyan]Subject:[/cyan] {message.subject}")
    console.print(f"[cyan]Body:[/cyan]")
    console.print(Panel(message.body[:2000], border_style="dim"))

    if thread_history:
        console.print(Rule("[dim]THREAD HISTORY[/dim]"))
        for m in thread_history[-3:]:
            console.print(
                f"[dim]{m['timestamp'][:10]} — {m['from_addr']}[/dim]"
            )
            console.print(Panel(
                (m['body'] or '')[:300],
                border_style="dim"
            ))


def _display_draft(draft: str) -> None:
    """Render the proposed draft reply."""
    console.print(Rule("[bold green]PROPOSED REPLY[/bold green]"))
    console.print(Panel(draft, border_style="green"))


def _open_in_editor(draft: str) -> str:
    """
    Open draft in $EDITOR (default: nano).
    Write draft to a temp file, open editor, read result on close.
    Returns the edited text (or original if editor fails).
    """
    editor = os.environ.get("EDITOR", "nano")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        prefix="daemon_wick_draft_"
    ) as f:
        f.write(draft)
        tmp_path = f.name

    try:
        subprocess.run([editor, tmp_path], check=True)
        with open(tmp_path) as f:
            edited = f.read().strip()
        return edited if edited else draft
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Editor failed: {e}. Returning original draft.")
        return draft
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def review(
    message: EmailMessage,
    draft: str,
    thread_history: list[dict],
) -> ApprovalResult:
    """
    Display the incoming email and proposed draft to the operator.
    Block until an explicit keyboard decision is made.

    Returns ApprovalResult with decision and final draft text.

    Keyboard options:
      [a] approve  — return approved, pass to sender
      [e] edit     — open draft in $EDITOR, re-display, confirm
      [s] skip     — discard draft, no action
      [f] flag     — mark for attention, no action
    """
    _display_thread(message, thread_history)
    _display_draft(draft)

    current_draft = draft

    while True:
        console.print(Rule())
        console.print(
            "[bold][a][/bold] approve  "
            "[bold][e][/bold] edit  "
            "[bold][s][/bold] skip  "
            "[bold][f][/bold] flag"
        )

        try:
            key = input("Decision: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted. Skipping.[/yellow]")
            return ApprovalResult(
                decision=ApprovalDecision.skipped,
                final_draft=None,
                original_draft=draft,
                edits_made=False,
            )

        if key == "a":
            console.print("[green]Approved.[/green]")
            return ApprovalResult(
                decision=ApprovalDecision.approved,
                final_draft=current_draft,
                original_draft=draft,
                edits_made=(current_draft != draft),
            )

        elif key == "e":
            console.print("[yellow]Opening editor...[/yellow]")
            edited = _open_in_editor(current_draft)
            current_draft = edited
            _display_draft(current_draft)
            console.print(
                "[yellow]Review your edits above, then choose again.[/yellow]"
            )
            # Loop continues — operator must press [a] to approve

        elif key == "s":
            console.print("[yellow]Skipped.[/yellow]")
            return ApprovalResult(
                decision=ApprovalDecision.skipped,
                final_draft=None,
                original_draft=draft,
                edits_made=False,
            )

        elif key == "f":
            console.print("[red]Flagged for operator attention.[/red]")
            return ApprovalResult(
                decision=ApprovalDecision.flagged,
                final_draft=None,
                original_draft=draft,
                edits_made=False,
            )

        else:
            console.print(
                "[red]Invalid key. Press a, e, s, or f.[/red]"
            )
