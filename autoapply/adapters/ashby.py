from playwright.async_api import Page

from ..schemas import FieldInventory, FormField, FieldType, FieldHints, PageButton


async def wait_for_form(page: Page) -> None:
    """Wait for React hydration. Timeout 15s."""
    try:
        await page.wait_for_selector(
            '.ashby-application-form-container, [data-testid="application-form"], form[class*="ashby"], form',
            timeout=15000,
        )
    except Exception:
        raise RuntimeError("Ashby form not found")


def _css_selector(el_info: dict) -> str:
    if el_info.get("id"):
        return f"#{el_info['id']}"
    if el_info.get("name"):
        return f'[name="{el_info["name"]}"]'
    return el_info.get("fallback_selector", "input")


async def extract_fields(page: Page, step: int = 0) -> FieldInventory:
    """Walk DOM, return FieldInventory for current step."""
    url = page.url

    fields_data = await page.evaluate("""() => {
        const form = document.querySelector('.ashby-application-form-container')
            || document.querySelector('[data-testid="application-form"]')
            || document.querySelector('form[class*="ashby"]')
            || document.querySelector('form')
            || document.body;
        if (!form) return { fields: [], buttons: [] };

        const fields = [];
        const seen_radio_groups = new Set();

        // Collect inputs, textareas, selects
        const elements = form.querySelectorAll('input, textarea, select');
        let idx = 0;
        for (const el of elements) {
            const tag = el.tagName.toLowerCase();
            let type = el.getAttribute('type') || (tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : 'text');
            type = type.toLowerCase();

            const elName = el.getAttribute('name') || '';
            const elId = el.getAttribute('id') || '';

            // Skip hidden, submit, and recaptcha inputs
            if (type === 'submit' || type === 'button') continue;
            if (elName === 'g-recaptcha-response' || elId.includes('recaptcha')) continue;
            if (type === 'hidden') continue;

            // Handle radio groups
            if (type === 'radio') {
                if (seen_radio_groups.has(elName)) continue;
                seen_radio_groups.add(elName);
                // Get labels for each radio option, not just values
                const radios = form.querySelectorAll(`input[name="${elName}"]`);
                const options = Array.from(radios).map(r => {
                    const rLabel = r.closest('label');
                    return rLabel ? rLabel.textContent.trim() : r.value;
                });
                const label = _findLabel(el, form);
                fields.push({
                    tag, type: 'radio', name: elName, id: elId, label,
                    placeholder: '',
                    aria_label: el.getAttribute('aria-label') || '',
                    required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true' || _isRequiredByLabel(el),
                    options,
                    idx,
                    fallback_selector: `input[name="${elName}"]`,
                    is_button_group: false,
                });
                idx++;
                continue;
            }

            // Detect Yes/No button pairs adjacent to checkbox fields
            if (type === 'checkbox') {
                const fieldEntry = el.closest('.ashby-application-form-field-entry, [class*="fieldEntry"]');
                if (fieldEntry) {
                    const siblingBtns = fieldEntry.querySelectorAll('button');
                    const btnTexts = Array.from(siblingBtns).map(b => b.textContent.trim());
                    if (btnTexts.includes('Yes') && btnTexts.includes('No')) {
                        const label = _findLabel(el, form);
                        fields.push({
                            tag: 'button-group', type: 'radio', name: elName, id: elId, label,
                            placeholder: '',
                            aria_label: el.getAttribute('aria-label') || '',
                            required: _isRequiredByLabel(el),
                            options: ['Yes', 'No'],
                            idx,
                            fallback_selector: elName ? `[name="${elName}"]` : `:nth-match(input, ${idx + 1})`,
                            is_button_group: true,
                        });
                        idx++;
                        continue;
                    }
                }
            }

            // Detect typeahead inputs
            const placeholder = el.getAttribute('placeholder') || '';
            const isTypeahead = placeholder.toLowerCase().includes('start typing')
                || el.hasAttribute('data-typeahead')
                || el.hasAttribute('data-autocomplete')
                || el.getAttribute('role') === 'combobox';

            // Select options
            let options = [];
            if (tag === 'select') {
                options = Array.from(el.querySelectorAll('option')).map(o => o.value);
            }

            const label = _findLabel(el, form);
            fields.push({
                tag, type, name: elName, id: elId, label,
                placeholder,
                aria_label: el.getAttribute('aria-label') || '',
                required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true' || _isRequiredByLabel(el),
                options,
                idx,
                fallback_selector: `:nth-match(${tag}, ${idx + 1})`,
                is_button_group: false,
                is_typeahead: isTypeahead,
            });
            idx++;
        }

        // Collect buttons - search form AND its parent (Ashby puts submit outside form container)
        const buttons = [];
        const btnScope = form.parentElement || form;
        const btnElements = btnScope.querySelectorAll('button, input[type="submit"]');
        for (const btn of btnElements) {
            const text = (btn.textContent || btn.value || '').trim().toLowerCase();
            const btnType = btn.getAttribute('type') || '';
            let role = 'unknown';
            if (btnType === 'submit' || text.includes('submit') || text.includes('apply')) {
                role = 'submit';
            } else if (text.includes('next') || text.includes('continue')) {
                role = 'next';
            } else if (text.includes('back') || text.includes('previous')) {
                role = 'back';
            }
            const btnId = btn.getAttribute('id') || '';
            const selector = btnId ? `#${btnId}` : `button:has-text("${(btn.textContent || '').trim()}")`;
            buttons.push({ role, selector, text: (btn.textContent || '').trim() });
        }

        function _isRequiredByLabel(el) {
            // Ashby marks required fields with _required class on the label
            const fieldEntry = el.closest('.ashby-application-form-field-entry, [class*="fieldEntry"]');
            if (fieldEntry) {
                const label = fieldEntry.querySelector('label');
                if (label && label.className.includes('required')) return true;
            }
            return false;
        }

        function _findLabel(el, form) {
            // aria-labelledby
            const labelledBy = el.getAttribute('aria-labelledby');
            if (labelledBy) {
                const labelEl = document.getElementById(labelledBy);
                if (labelEl) return labelEl.textContent.trim();
            }
            // <label for="">
            const labelForId = el.getAttribute('id');
            if (labelForId) {
                const labelEl = form.querySelector(`label[for="${labelForId}"]`);
                if (labelEl) return labelEl.textContent.trim();
            }
            // Parent <label>
            const parent = el.closest('label');
            if (parent) return parent.textContent.trim();
            // Ashby field-entry container label
            const fieldEntry = el.closest('.ashby-application-form-field-entry, [class*="fieldEntry"]');
            if (fieldEntry) {
                const containerLabel = fieldEntry.querySelector('label');
                if (containerLabel) return containerLabel.textContent.trim();
            }
            // Previous sibling or nearby text
            const prev = el.previousElementSibling;
            if (prev && prev.tagName === 'LABEL') return prev.textContent.trim();
            return '';
        }

        return { fields, buttons };
    }""")

    form_fields = []
    for f in fields_data.get("fields", []):
        type_str = f["type"]
        if type_str not in [e.value for e in FieldType]:
            type_str = "text"
        selector = _css_selector(f)

        # Signal typeahead or button-group via hints.autocomplete
        autocomplete_hint = None
        if f.get("is_typeahead"):
            autocomplete_hint = "typeahead"
        elif f.get("is_button_group"):
            autocomplete_hint = "button-group"

        form_fields.append(FormField(
            field_id=f.get("id") or f.get("name") or f"field_{f['idx']}",
            label=f.get("label", ""),
            type=FieldType(type_str),
            required=f.get("required", False),
            selector=selector,
            options=f.get("options", []),
            hints=FieldHints(
                name=f.get("name") or None,
                id=f.get("id") or None,
                placeholder=f.get("placeholder") or None,
                aria_label=f.get("aria_label") or None,
                autocomplete=autocomplete_hint,
            ),
            step=step,
        ))

    buttons = []
    for b in fields_data.get("buttons", []):
        buttons.append(PageButton(
            role=b["role"],
            selector=b["selector"],
            text=b["text"],
        ))

    return FieldInventory(url=url, step=step, fields=form_fields, buttons=buttons)
