import sys
import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from .poller import EmailMessage

logger = logging.getLogger(__name__)
console = Console()

DEFAULT_KILL_WINDOW = 60   # seconds


class AutoSendDecision(str, Enum):
    sent    = "sent"      # countdown elapsed, auto-sent
    aborted = "aborted"   # operator pressed Ctrl+C


@dataclass
class AutoSendResult:
    decision: AutoSendDecision
    draft: str            # the draft that was (or would have been) sent
    elapsed: float        # seconds elapsed before decision


def _display_pending(
    message: EmailMessage,
    draft: str,
    kill_window: int,
) -> None:
    """
    Print the incoming email, proposed draft, and kill window
    instructions to the terminal. Non-blocking — just display.
    """
    console.print(Rule("[bold cyan]AUTO-SEND PENDING[/bold cyan]"))
    console.print(f"[cyan]From:[/cyan]    {message.from_addr}")
    console.print(f"[cyan]Subject:[/cyan] {message.subject}")
    console.print(Rule("[bold green]PROPOSED REPLY[/bold green]"))
    console.print(Panel(draft, border_style="green"))
    console.print(Rule())
    console.print(
        f"[yellow]Sending in {kill_window}s. "
        f"Press Ctrl+C to abort.[/yellow]"
    )


def _countdown(kill_window: int) -> AutoSendDecision:
    """
    Count down kill_window seconds, printing progress each second.
    Returns AutoSendDecision.sent on completion.
    Raises KeyboardInterrupt if operator aborts — caller catches it.
    """
    for remaining in range(kill_window, 0, -1):
        sys.stdout.write(f"\r  Sending in {remaining:2d}s... ")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r  Sending now...        \n")
    sys.stdout.flush()
    return AutoSendDecision.sent


def review_and_send(
    message: EmailMessage,
    draft: str,
    kill_window: int = DEFAULT_KILL_WINDOW,
) -> AutoSendResult:
    """
    Display the draft and count down to auto-send.

    If kill_window elapses without interruption: returns
    AutoSendResult(decision=sent, ...).

    If operator presses Ctrl+C: returns
    AutoSendResult(decision=aborted, ...).

    Never raises. All KeyboardInterrupts are caught internally.
    The caller is responsible for actually calling sender.send()
    based on the returned decision.

    Args:
        message:     The EmailMessage being replied to
        draft:       The proposed reply text
        kill_window: Seconds to wait before auto-send (default 60)

    Returns:
        AutoSendResult with decision and draft
    """
    _display_pending(message, draft, kill_window)

    start = time.monotonic()

    try:
        decision = _countdown(kill_window)
        elapsed = time.monotonic() - start
        logger.info(
            f"Kill window elapsed ({elapsed:.1f}s). "
            f"Auto-sending to {message.from_addr}."
        )
        return AutoSendResult(
            decision=decision,
            draft=draft,
            elapsed=elapsed,
        )

    except KeyboardInterrupt:
        elapsed = time.monotonic() - start
        console.print(
            f"\n[red]Aborted after {elapsed:.1f}s. "
            f"Nothing sent.[/red]"
        )
        logger.info(
            f"Auto-send aborted by operator after {elapsed:.1f}s."
        )
        return AutoSendResult(
            decision=AutoSendDecision.aborted,
            draft=draft,
            elapsed=elapsed,
        )
