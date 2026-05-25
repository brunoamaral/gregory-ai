from django import forms
from django.forms import ModelForm
from django_ckeditor_5.widgets import CKEditor5Widget

from .models import Subscribers, Lists, Announcement, SubscriberSiteProfile

class ListsAdminForm(ModelForm):
    ml_threshold = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        widget=forms.NumberInput(attrs={
            'step': '0.01',
            'min': '0.0',
            'max': '1.0',
            'style': 'width: 100px;'
        }),
        help_text='ML prediction confidence threshold (0.0-1.0). Only articles with ML predictions above this threshold will be considered relevant. Use increments of 0.01.',
    )

    class Meta:
        model = Lists
        fields = '__all__'
        help_texts = {
            'subjects': 'Select subjects for relevant articles and trials in the main content of your emails.',
            'latest_research_categories': 'Select team categories to show the latest research for in a dedicated section.',
        }
        labels = {
            'subjects': 'Subjects for Main Content',
            'latest_research_categories': 'Team Categories for Latest Research'
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('ml_threshold') is None:
            cleaned['ml_threshold'] = 0.8
        return cleaned


class AnnouncementAdminForm(ModelForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if not self.request:
            return

        from gregory.admin import get_user_organizations
        from organizations.models import Organization

        user = self.request.user
        user_org_ids = get_user_organizations(user)

        # Scope the organization choices and pick the default.
        if user.is_superuser:
            org_qs = Organization.objects.order_by('pk')
            membership = (
                user.organizations_organizationuser.order_by('pk').first()
            )
            default_org = (
                membership.organization if membership else org_qs.first()
            )
        else:
            org_qs = Organization.objects.filter(
                pk__in=list(user_org_ids or [])
            ).order_by('pk')
            default_org = org_qs.first()

        # Sent announcements get organization/lists moved to readonly by the
        # admin, which removes them from self.fields. Skip the scoping logic
        # in that case — there's nothing editable to scope.
        if 'organization' in self.fields:
            self.fields['organization'].queryset = org_qs
            if not self.instance.pk and default_org is not None:
                self.fields['organization'].initial = default_org
            # Lock the field when the user has only one valid choice.
            if org_qs.count() <= 1:
                self.fields['organization'].disabled = True

            # Lock the field post-send (defence in depth — admin also gates it).
            if self.instance.pk and self.instance.status == 'sent':
                self.fields['organization'].disabled = True

        if 'lists' in self.fields:
            # Scope list choices to the currently selected (or saved) org.
            # Priority: (1) POSTed org value, (2) saved instance org on edit,
            # (3) default org for new announcements.
            current_org = (
                self.data.get('organization')
                or (self.instance.organization_id if self.instance.pk else None)
                or (default_org.pk if default_org else None)
            )
            if current_org:
                self.fields['lists'].queryset = Lists.objects.filter(
                    team__organization_id=current_org
                )
            elif user_org_ids is not None:
                # Non-superuser with no org yet picked: limit to their orgs.
                self.fields['lists'].queryset = Lists.objects.filter(
                    team__organization__id__in=user_org_ids
                )

    def clean(self):
        cleaned = super().clean()
        org = cleaned.get('organization')
        lists_selected = cleaned.get('lists') or []
        if org and lists_selected:
            offenders = [
                lst.list_name
                for lst in lists_selected
                if lst.team.organization_id != org.pk
            ]
            if offenders:
                raise forms.ValidationError({
                    'lists': (
                        "Lists must belong to the same organization as the "
                        "announcement. Offending lists: "
                        + ", ".join(offenders)
                    ),
                })
        return cleaned

    def clean_body(self):
        """Reject any <img> that lacks a non-empty alt attribute."""
        from bs4 import BeautifulSoup
        body = self.cleaned_data.get('body', '')
        soup = BeautifulSoup(body, 'html.parser')
        imgs = soup.find_all('img')
        offenders = [
            str(i)
            for i, img in enumerate(imgs, start=1)
            if not img.get('alt', '').strip()
        ]
        if offenders:
            total = len(imgs)
            raise forms.ValidationError(
                f"Alt text is required for image(s) {', '.join(offenders)} of {total}. "
                "Add a description in the image properties dialog."
            )
        return body

    class Meta:
        model = Announcement
        fields = ['subject', 'header_title', 'header_tagline', 'show_header_tagline',
                  'preheader_text', 'body', 'lists', 'organization']
        widgets = {
            'body': CKEditor5Widget(config_name='default'),
            'lists': forms.CheckboxSelectMultiple,
        }


class SubscribersForm(ModelForm):
	first_name = forms.CharField(max_length=100)
	last_name = forms.CharField(max_length=100, required=False)
	email = forms.EmailField(max_length=120)
	profile = forms.ChoiceField(
		choices=[('', '---------')] + SubscriberSiteProfile.PROFILEOPTIONS,
		required=False,
	)
	# ``list`` was previously an ``IntegerField`` which only captured a
	# single checkbox value.  It has been removed so the view can obtain all
	# selected list IDs using ``request.POST.getlist('list')``.
	class Meta:
		model = Subscribers
		fields = [
			'first_name',
			'last_name',
		]
		exclude = ['subscriber_id', 'active', 'is_admin', 'email']



