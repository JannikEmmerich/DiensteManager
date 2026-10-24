from django import forms, template

register = template.Library()


@register.filter
def bs_widget(field):
    """Renders a form field's widget with Bootstrap classes."""
    widget = field.field.widget
    if isinstance(widget, forms.CheckboxInput | forms.RadioSelect | forms.CheckboxSelectMultiple):
        css = "form-check-input"
    elif isinstance(widget, forms.Select):
        css = "form-select"
    else:
        css = "form-control"
    if field.errors:
        css += " is-invalid"
    existing = widget.attrs.get("class", "")
    return field.as_widget(attrs={"class": f"{existing} {css}".strip()})


@register.filter
def is_checkbox(field):
    return isinstance(field.field.widget, forms.CheckboxInput)
