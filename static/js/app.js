// Small behaviours of the pages. They live here instead of in onclick/onsubmit
// attributes because the Content Security Policy (SECURE_CSP) forbids inline scripts.

// Asks before submitting forms (or with buttons) that carry data-confirm="Question?".
document.addEventListener("submit", (event) => {
    const question = event.submitter?.dataset.confirm || event.target.dataset.confirm;
    if (question && !window.confirm(question)) {
        event.preventDefault();
    }
});

// Submits the form as soon as a field with data-submit-on-change changes.
document.addEventListener("change", (event) => {
    if (event.target.matches("[data-submit-on-change]")) {
        event.target.form.submit();
    }
});

// Shows or hides elements with data-hidden-if="field=value": hidden while the field
// (radio buttons or a select) has that value, e.g. the rates while there is no duty pay.
// Fields inside are disabled meanwhile, so that they are neither checked nor sent.
function updateHiddenIf() {
    for (const element of document.querySelectorAll("[data-hidden-if]")) {
        const [name, value] = element.dataset.hiddenIf.split("=");
        const form = element.closest("form");
        const field =
            form?.querySelector(`input[name="${name}"]:checked`) ||
            form?.querySelector(`select[name="${name}"]`);
        element.hidden = field?.value === value;
        for (const field of element.querySelectorAll("input, select, textarea")) {
            field.disabled = element.hidden;
        }
    }
}

document.addEventListener("change", (event) => {
    if (event.target.matches("input[type=radio], select")) {
        updateHiddenIf();
    }
});
updateHiddenIf();

// Rows of formsets (data-formset="prefix"): "Add" copies the empty row from the
// <template>, "Remove" deletes a row that has not been saved yet. Django numbers the
// fields of the rows (prefix-0-name, prefix-1-name, …) and reads the number of rows from
// prefix-TOTAL_FORMS, so both are kept up to date. Saved rows always come first and are
// never removed here, so their numbers stay the same.
function renumberRows(formset) {
    const prefix = formset.dataset.formset;
    const pattern = new RegExp(`${prefix}-(\\d+|__prefix__)-`);
    const rows = formset.querySelectorAll("[data-formset-rows] > [data-formset-row]");
    rows.forEach((row, index) => {
        for (const element of row.querySelectorAll("[name], [id], [for]")) {
            for (const attribute of ["name", "id", "for"]) {
                const value = element.getAttribute(attribute);
                if (value) {
                    element.setAttribute(attribute, value.replace(pattern, `${prefix}-${index}-`));
                }
            }
        }
    });
    formset.querySelector(`[name="${prefix}-TOTAL_FORMS"]`).value = rows.length;
}

document.addEventListener("click", (event) => {
    const add = event.target.closest("[data-formset-add]");
    const remove = event.target.closest("[data-formset-remove]");
    const formset = (add || remove)?.closest("[data-formset]");
    if (!formset) {
        return;
    }
    if (add) {
        const template = formset.querySelector("template[data-formset-template]");
        const row = template.content.firstElementChild.cloneNode(true);
        formset.querySelector("[data-formset-rows]").append(row);
        renumberRows(formset);
        updateHiddenIf();
        row.querySelector("input:not([type=hidden]), select, textarea")?.focus();
    } else {
        remove.closest("[data-formset-row]").remove();
        renumberRows(formset);
    }
});

// The buttons that add rows only work with this script.
for (const button of document.querySelectorAll("[data-formset-add]")) {
    button.hidden = false;
}
