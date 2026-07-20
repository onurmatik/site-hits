from django import forms
from django.forms import BaseModelFormSet, modelformset_factory

from analytics.models import ActivationDefinition, ProductEventDefinition
from analytics.privacy import EVENT_NAME_PATTERN, PROPERTY_NAME_PATTERN


INPUT_CLASS = (
    "min-h-11 w-full rounded-[2px] border border-ink/20 bg-white px-3 "
    "outline-none focus:border-forest"
)


class ProductEventDefinitionForm(forms.ModelForm):
    class Meta:
        model = ProductEventDefinition
        fields = ("event_name", "display_name", "description", "aggregation", "unit")
        widgets = {
            "event_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "purchase"}),
            "display_name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Completed purchase"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": f"{INPUT_CLASS} min-h-24 py-3",
                    "rows": 3,
                    "placeholder": "Fire after payment is durably confirmed.",
                }
            ),
            "aggregation": forms.Select(attrs={"class": INPUT_CLASS}),
            "unit": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "TRY, seconds, items"}
            ),
        }

    def clean_event_name(self):
        value = self.cleaned_data["event_name"].strip().lower()
        if not EVENT_NAME_PATTERN.fullmatch(value):
            raise forms.ValidationError(
                "Use lowercase letters, numbers, underscores, colons, or hyphens."
            )
        return value

    def clean_unit(self):
        value = self.cleaned_data.get("unit", "").strip()
        if value and not PROPERTY_NAME_PATTERN.fullmatch(value.lower()):
            raise forms.ValidationError("Use a short unit such as TRY, seconds, or items.")
        return value


class BaseProductEventDefinitionFormSet(BaseModelFormSet):
    def __init__(self, *args, site, **kwargs):
        self.site = site
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.instance.site = site

    def clean(self):
        super().clean()
        names = set()
        activation = ActivationDefinition.objects.filter(site=self.site).first()
        protected_ids = (
            {activation.start_event_id, activation.goal_event_id} if activation else set()
        )
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                if form.instance.pk in protected_ids:
                    form.add_error(
                        "DELETE",
                        "Remove or change the activation funnel before deleting this event.",
                    )
                continue
            name = form.cleaned_data.get("event_name")
            if name and name in names:
                form.add_error("event_name", "Event names must be unique within a site.")
            names.add(name)


ProductEventDefinitionFormSet = modelformset_factory(
    ProductEventDefinition,
    form=ProductEventDefinitionForm,
    formset=BaseProductEventDefinitionFormSet,
    extra=1,
    can_delete=True,
)


class ActivationDefinitionForm(forms.ModelForm):
    enabled = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = ActivationDefinition
        fields = ("start_event", "goal_event")
        widgets = {
            "start_event": forms.Select(attrs={"class": INPUT_CLASS}),
            "goal_event": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, site, **kwargs):
        self.site = site
        super().__init__(*args, **kwargs)
        queryset = ProductEventDefinition.objects.filter(site=site)
        self.fields["start_event"].queryset = queryset
        self.fields["goal_event"].queryset = queryset
        self.fields["start_event"].required = False
        self.fields["goal_event"].required = False
        self.fields["enabled"].initial = bool(self.instance and self.instance.pk)
        self.fields["enabled"].widget.attrs.update({"class": "size-4 accent-forest"})

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("enabled"):
            return cleaned
        if not cleaned.get("start_event"):
            self.add_error("start_event", "Choose an activation start event.")
        if not cleaned.get("goal_event"):
            self.add_error("goal_event", "Choose an activation goal event.")
        if cleaned.get("start_event") == cleaned.get("goal_event"):
            raise forms.ValidationError("Activation start and goal events must be different.")
        return cleaned
