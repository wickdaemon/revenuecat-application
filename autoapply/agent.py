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
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="networkidle")

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
                        await runner.click_submit(inventory, wait_for_operator=True)
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
            await browser.close()

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
