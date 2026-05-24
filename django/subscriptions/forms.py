from django import forms
from django.forms import ModelForm
from django_ckeditor_5.widgets import CKEditor5Widget

from .models import Subscribers, Lists, Announcement, SubscriberSiteProfile

class ListsAdminForm(ModelForm):
    class Meta:
        model = Lists
        fields = '__all__'
        widgets = {
            'ml_threshold': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0.0',
                'max': '1.0',
                'style': 'width: 100px;'
            })
        }
        help_texts = {
            'subjects': 'Select subjects for relevant articles and trials in the main content of your emails.',
            'latest_research_categories': 'Select team categories to show the latest research for in a dedicated section.',
            'ml_threshold': 'ML prediction confidence threshold (0.0-1.0). Only articles with ML predictions above this threshold will be considered relevant. Use increments of 0.01.'
        }
        labels = {
            'subjects': 'Subjects for Main Content',
            'latest_research_categories': 'Team Categories for Latest Research'
        }


class AnnouncementAdminForm(ModelForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request:
            from gregory.admin import get_user_organizations
            user_orgs = get_user_organizations(self.request.user)
            if user_orgs is not None:
                self.fields['lists'].queryset = Lists.objects.filter(
                    team__organization__id__in=user_orgs
                )

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
        fields = ['subject', 'header_title', 'header_tagline', 'show_header_tagline', 'preheader_text', 'body', 'lists']
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



