import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

APPLICATION_MD = Path(__file__).parent / "application.md"


def publish_gist(description: str = "Daemon Wick — Application Letter") -> str:
    """
    Publish daemon/application.md as a public GitHub Gist using `gh`.

    Returns the Gist URL on success.
    Raises RuntimeError if `gh` is not available or the command fails.
    """
    if not APPLICATION_MD.exists():
        raise FileNotFoundError(f"{APPLICATION_MD} not found")

    result = subprocess.run(
        [
            "gh", "gist", "create",
            str(APPLICATION_MD),
            "--public",
            "--desc", description,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"gh gist create failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    url = result.stdout.strip()
    logger.info(f"Gist published: {url}")
    return url


def get_existing_gist_url() -> str | None:
    """
    Check if a Gist already exists with application.md.
    Returns the URL if found, None otherwise.
    """
    result = subprocess.run(
        ["gh", "gist", "list", "--limit", "20"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    for line in result.stdout.strip().splitlines():
        if "application.md" in line.lower() or "daemon wick" in line.lower():
            # gh gist list format: ID\tDESCRIPTION\tFILES\tVISIBILITY\tUPDATED
            gist_id = line.split("\t")[0].strip()
            return f"https://gist.github.com/{gist_id}"

    return None
