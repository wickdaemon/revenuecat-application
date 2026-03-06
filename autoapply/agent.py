import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright
from rich import print as rprint

from .schemas import Profile, RunResult, MappingSource, ActionRecord
from .adapters.ashby import wait_for_form, extract_fields
from .mapper import heuristic_map
from .backends.ollama import llm_map
from .runner import Runner


MAX_STEPS = 10

# Persistent Chrome profile — cookies, localStorage, reCAPTCHA v3 scores persist.
CHROME_PROFILE_DIR = str(Path.home() / ".autoapply" / "chrome-profile")


def validate_profile_for_submission(profile: Profile) -> list[str]:
    """Returns list of warnings about profile values that may cause form rejection."""
    warnings = []

    op_name = profile.answers.operator_name or ""
    if op_name and " " not in op_name.strip():
        warnings.append(
            f"operator_name '{op_name}' contains no space — Ashby may "
            f"require a first and last name. Consider 'Undisclosed Operator' "
            f"or update in the browser at the CAPTCHA gate."
        )

    if not profile.answers.application_url:
        warnings.append("application_url is not set — required field will be empty.")

    if not profile.answers.gdpr_consent:
        warnings.append("gdpr_consent is not set — GDPR radio will be unfilled.")

    return warnings


async def _handle_captcha(page, runner) -> None:
    """
    Detect and handle reCAPTCHA before submit.
    v3 (invisible): wait for score evaluation.
    v2 (checkbox): block until operator solves it.
    """
    frame = await page.query_selector(
        "iframe[src*='recaptcha'], iframe[title*='reCAPTCHA']"
    )
    if not frame:
        return  # no captcha present

    v2 = await page.query_selector(".recaptcha-checkbox")
    if v2:
        runner._record("captcha_gate", "", "", 0, "reCAPTCHA v2 — waiting for operator")
        print("\n⚠  reCAPTCHA detected. Solve it in the browser window.")
        print("   Press ENTER here when done.")
        input()
    else:
        runner._record("captcha_gate", "", "", 0, "reCAPTCHA v3 — waiting 3s")
        await asyncio.sleep(3.0)


async def run(
    url: str,
    profile: Profile,
    dry_run: bool = True,
    headless: bool = True,
    artifacts_dir: str = "runs/latest",
    use_llm: bool = True,
    llm_model: str = "qwen2.5:3b",
    llm_base: str = "http://localhost:11434",
) -> RunResult:
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)
    mode = "dry_run" if dry_run else "live"

    # Pre-flight validation for live submissions
    if not dry_run:
        warnings = validate_profile_for_submission(profile)
        if warnings:
            for w in warnings:
                rprint(f"[yellow][WARNING] {w}[/yellow]")
            rprint("[yellow]Warnings found. Review above before proceeding.[/yellow]")
            rprint("[yellow]Press ENTER to continue anyway, or Ctrl+C to abort...[/yellow]")
            input()
    submitted = False
    step_num = 0
    error_msg = None

    async with async_playwright() as pw:
        profile_dir = Path(CHROME_PROFILE_DIR)
        profile_dir.mkdir(parents=True, exist_ok=True)

        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Remove navigator.webdriver flag
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        try:
            await page.goto(url, wait_until="networkidle")

            # Human behavior simulation before interacting with form
            pre_runner = Runner(page, dry_run=dry_run)
            await pre_runner.simulate_human_arrival()

            for step_num in range(MAX_STEPS):
                await wait_for_form(page)
                inventory = await extract_fields(page, step=step_num)

                mapping = heuristic_map(inventory, profile)

                # LLM fallback for unfilled required fields
                if use_llm and mapping.unfilled_required:
                    unfilled_fields = [
                        f for f in inventory.fields
                        if f.field_id in mapping.unfilled_required
                    ]
                    if unfilled_fields:
                        try:
                            llm_mapping = await llm_map(
                                unfilled_fields, profile,
                                model=llm_model, base_url=llm_base,
                            )
                            # Merge LLM decisions into mapping
                            llm_by_id = {d.field_id: d for d in llm_mapping.decisions}
                            for i, d in enumerate(mapping.decisions):
                                if d.field_id in llm_by_id and d.source == MappingSource.skip:
                                    mapping.decisions[i] = llm_by_id[d.field_id]
                            mapping.unfilled_required = [
                                fid for fid in mapping.unfilled_required
                                if fid not in llm_by_id or llm_by_id[fid].value == ""
                            ]
                        except Exception:
                            pass  # LLM is best-effort

                runner = Runner(page, dry_run=dry_run)
                await runner.execute_mapping(mapping, inventory)

                has_submit = any(b.role == "submit" for b in inventory.buttons)
                has_next = any(b.role == "next" for b in inventory.buttons)

                if has_submit and (not has_next or step_num == MAX_STEPS - 1):
                    if not dry_run:
                        await _handle_captcha(page, runner)
                        await runner.click_submit(inventory, wait_for_operator=True)

                        # Wait for Ashby to process and render confirmation
                        try:
                            await page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass  # networkidle timeout is non-fatal

                        # Capture post-submit state
                        post_screenshot = os.path.join(artifacts_dir, "post-submit.png")
                        await page.screenshot(path=post_screenshot, full_page=True)

                        post_submit_url = page.url
                        post_submit_title = await page.title()
                        runner._record(
                            "post_submit", "", "", step_num,
                            f"url={post_submit_url} title={post_submit_title}",
                        )

                        # Give operator time to see the confirmation page
                        await asyncio.sleep(5)
                    else:
                        runner._record("click", next(
                            (b.selector for b in inventory.buttons if b.role == "submit"), ""
                        ), "", step_num, "would click submit (dry run)")
                    submitted = not dry_run
                    # Save action log
                    actions_path = os.path.join(artifacts_dir, "actions.json")
                    with open(actions_path, "w") as f:
                        json.dump([r.model_dump() for r in runner.log], f, indent=2)
                    break
                elif has_next:
                    await runner.click_next(inventory)
                else:
                    # Save action log even if no navigation
                    actions_path = os.path.join(artifacts_dir, "actions.json")
                    with open(actions_path, "w") as f:
                        json.dump([r.model_dump() for r in runner.log], f, indent=2)
                    break

                # Save action log for this step
                actions_path = os.path.join(artifacts_dir, "actions.json")
                with open(actions_path, "w") as f:
                    json.dump([r.model_dump() for r in runner.log], f, indent=2)

            # Final screenshot
            screenshot_path = os.path.join(artifacts_dir, "final.png")
            await page.screenshot(path=screenshot_path, full_page=True)

        except Exception as e:
            error_msg = str(e)
            error_screenshot = os.path.join(artifacts_dir, "error.png")
            try:
                await page.screenshot(path=error_screenshot, full_page=True)
            except Exception:
                pass
        finally:
            await context.close()

    status = "error" if error_msg else "success"
    return RunResult(
        url=url,
        profile="profile",
        mode=mode,
        status=status,
        steps=step_num + 1,
        submitted=submitted,
        error=error_msg,
        artifacts_dir=artifacts_dir,
    )
