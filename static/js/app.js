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
