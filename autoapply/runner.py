import asyncio

from playwright.async_api import Page

from .schemas import (
    ActionRecord, FieldInventory, Mapping, MappingSource, FieldType, FormField,
)

# Labels that get slow (human-like) typing — visible text inputs a human would type into.
_SLOW_LABEL_KEYWORDS = ("name", "location", "why", "links", "letter")


class Runner:
    def __init__(self, page: Page, dry_run: bool = True):
        self.page = page
        self.dry_run = dry_run
        self.log: list[ActionRecord] = []
        self._seq = 0

    async def simulate_human_arrival(self) -> None:
        """Scroll and pause before filling — improves reCAPTCHA v3 score."""
        self._record("simulate", "", "", 0, "human arrival")
        if self.dry_run:
            return
        # Slow scroll down
        await self.page.evaluate("""
            () => new Promise(resolve => {
                let y = 0;
                const id = setInterval(() => {
                    y += 60;
                    window.scrollTo(0, y);
                    if (y >= 300) { clearInterval(id); resolve(); }
                }, 100);
            })
        """)
        await self.page.mouse.move(640, 400)
        await asyncio.sleep(2.5)
        await self.page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

    def _record(self, type: str, selector: str, value: str, step: int, note: str = "") -> ActionRecord:
        self._seq += 1
        rec = ActionRecord(seq=self._seq, type=type, selector=selector, value=value, step=step, note=note)
        self.log.append(rec)
        return rec

    async def _fill_text(self, selector: str, value: str, step: int,
                         note: str = "", slow: bool = False) -> None:
        self._record("fill", selector, value, step, note)
        if self.dry_run:
            return
        el = await self.page.wait_for_selector(selector, timeout=5000)
        if slow:
            await el.click()
            await el.type(value, delay=40)  # 40ms per keystroke
        else:
            await el.fill(value)

    async def _verify_typeahead_resolved(self, selector: str, typed_value: str) -> bool:
        """Check that a typeahead input resolved to a valid selection."""
        await self.page.wait_for_timeout(500)
        try:
            current = await self.page.input_value(selector)
            return bool(current) and len(current) >= len(typed_value)
        except Exception:
            return False

    async def _fill_typeahead(self, selector: str, value: str, step: int, note: str = "") -> None:
        self._record("fill", selector, value, step, note + " [typeahead]" if note else "typeahead")
        if self.dry_run:
            return
        # Click to focus
        await self.page.click(selector)
        # Type character by character to trigger dropdown
        await self.page.type(selector, value, delay=80)
        # Wait for dropdown options
        try:
            await self.page.wait_for_selector(
                '[role="listbox"], [role="option"], [class*="typeahead"], [class*="dropdown"] li, [class*="option"]',
                timeout=10000,
            )
            # Try to click the first option that contains our value
            options = await self.page.query_selector_all(
                '[role="option"], [class*="typeahead"] li, [class*="dropdown"] li, [class*="option"]'
            )
            clicked = False
            for opt in options:
                text = (await opt.inner_text()).strip()
                if value.lower() in text.lower():
                    await opt.click()
                    clicked = True
                    break
            if not clicked and options:
                await options[0].click()
        except Exception:
            # Dropdown never appeared — fall back to fill
            await self.page.fill(selector, "")
            await self.page.fill(selector, value)
            self.log[-1].note += " [typeahead fallback to fill]"

        # Post-selection verification
        if await self._verify_typeahead_resolved(selector, value):
            self.log[-1].note += " [typeahead resolved]"
        else:
            self.log[-1].note += " [WARN: typeahead unresolved — verify in browser]"

    async def _select(self, selector: str, value: str, step: int, note: str = "") -> None:
        self._record("select", selector, value, step, note)
        if self.dry_run:
            return
        await self.page.select_option(selector, value)

    async def _check(self, selector: str, value: str, step: int, note: str = "") -> None:
        self._record("check", selector, value, step, note)
        if self.dry_run:
            return
        await self.page.check(selector)

    async def _upload(self, selector: str, path: str, step: int, note: str = "") -> None:
        self._record("upload", selector, path, step, note)
        if self.dry_run:
            return
        await self.page.set_input_files(selector, path)

    async def _click(self, selector: str, step: int, note: str = "") -> None:
        self._record("click", selector, "", step, note)
        if self.dry_run:
            return
        await self.page.click(selector)

    async def _click_option(self, selector: str, option_text: str, step: int, note: str = "") -> None:
        """Click a button within a field's parent container matching option_text."""
        self._record("click", selector, option_text, step, note + f" [button-group: {option_text}]")
        if self.dry_run:
            return
        # Find the field element, then search its parent container for buttons
        field_el = await self.page.query_selector(selector)
        if field_el:
            container = await field_el.evaluate_handle(
                'el => el.closest(".ashby-application-form-field-entry") || el.closest("[class*=fieldEntry]") || el.parentElement'
            )
            buttons = await container.query_selector_all("button")
            for btn in buttons:
                text = (await btn.inner_text()).strip()
                if text.lower() == option_text.lower():
                    await btn.click()
                    return
        # Fallback: use Playwright text selector
        await self.page.click(f'button:has-text("{option_text}")')

    async def _click_radio_option(self, field: FormField, value: str, step: int, note: str = "") -> None:
        """Click the radio option whose label text contains value (fuzzy match)."""
        self._record("click", field.selector, value, step, note + " [radio-select]")
        if self.dry_run:
            return
        # Find all radio inputs in this group by name
        name = field.hints.name
        if name:
            radios = await self.page.query_selector_all(f'input[name="{name}"]')
            for radio in radios:
                # Check the parent label text
                label_text = await radio.evaluate('el => { const l = el.closest("label"); return l ? l.textContent.trim() : ""; }')
                if value.lower() in label_text.lower():
                    await radio.click()
                    return
            # If no fuzzy match, try the surrounding field entry
            field_el = await self.page.query_selector(field.selector)
            if field_el:
                container = await field_el.evaluate_handle(
                    'el => el.closest(".ashby-application-form-field-entry") || el.closest("[class*=fieldEntry]") || el.parentElement'
                )
                labels = await container.query_selector_all("label")
                for label in labels:
                    text = (await label.inner_text()).strip()
                    if value.lower() in text.lower():
                        await label.click()
                        return

    async def execute_mapping(self, mapping: Mapping, inventory: FieldInventory) -> None:
        field_map = {f.field_id: f for f in inventory.fields}
        for decision in mapping.decisions:
            if decision.source == MappingSource.skip:
                continue
            if decision.value == "":
                continue
            field = field_map.get(decision.field_id)
            ft = field.type if field else FieldType.text

            if ft == FieldType.radio:
                if field and field.hints.autocomplete == "button-group":
                    # Yes/No button group
                    await self._click_option(decision.selector, decision.value, inventory.step, decision.note)
                else:
                    # Standard radio or labeled radio
                    await self._click_radio_option(field, decision.value, inventory.step, decision.note)
            elif ft == FieldType.select:
                await self._select(decision.selector, decision.value, inventory.step, decision.note)
            elif ft == FieldType.checkbox:
                await self._check(decision.selector, decision.value, inventory.step, decision.note)
            elif ft == FieldType.file:
                await self._upload(decision.selector, decision.value, inventory.step, decision.note)
            elif field and field.hints.autocomplete == "typeahead":
                await self._fill_typeahead(decision.selector, decision.value, inventory.step, decision.note)
            else:
                label_lower = (field.label.lower() if field else "")
                use_slow = any(kw in label_lower for kw in _SLOW_LABEL_KEYWORDS)
                await self._fill_text(
                    decision.selector, decision.value, inventory.step,
                    decision.note, slow=use_slow,
                )

    async def click_next(self, inventory: FieldInventory) -> bool:
        for btn in inventory.buttons:
            if btn.role == "next":
                await self._click(btn.selector, inventory.step, "click next")
                return True
        return False

    async def click_submit(self, inventory: FieldInventory, wait_for_operator: bool = False) -> bool:
        for btn in inventory.buttons:
            if btn.role == "submit":
                if wait_for_operator:
                    print(
                        "\n[CAPTCHA GATE] Form is filled. Before pressing ENTER:\n"
                        "  1. Check that the location field shows a fully resolved value\n"
                        "     (should read \"San Francisco, CA, USA\" or similar — not empty)\n"
                        "  2. Solve any CAPTCHA challenge (may be invisible — check for checkbox)\n"
                        "  3. Visually confirm all other fields look correct\n"
                        "Press ENTER when ready to submit..."
                    )
                    input()
                await self._click(btn.selector, inventory.step, "click submit")
                return True
        return False

    async def screenshot(self, path: str) -> None:
        await self.page.screenshot(path=path, full_page=True)
