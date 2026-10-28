import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import (
    LEAD_SOURCE_CHOICES,
    Company,
    Contact,
    Deal,
    Lead,
    Note,
    Task,
)


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'autocomplete': 'email',
            'placeholder': 'you@example.com',
        }),
    )

    class Meta(UserCreationForm.Meta):
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'autocomplete': 'username',
            'placeholder': 'Choose a username',
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'autocomplete': 'new-password',
            'placeholder': 'Create a password',
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'autocomplete': 'new-password',
            'placeholder': 'Confirm your password',
        })

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class EmailOrUsernameAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label='Username or email',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'autocomplete': 'username',
            'autofocus': True,
            'placeholder': 'Your username or email',
        }),
    )

    password = forms.CharField(
        label='Password',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'current-password',
            'placeholder': 'Your password',
        }),
    )

    def clean(self):
        username = self.cleaned_data.get('username')
        if username and '@' in username:
            matching_users = get_user_model().objects.filter(email__iexact=username)
            if matching_users.count() == 1:
                self.cleaned_data['username'] = matching_users.get().get_username()
        return super().clean()


class StyledPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update({
            'class': 'form-control',
            'autocomplete': 'email',
            'placeholder': 'you@example.com',
            'autofocus': True,
        })


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].widget.attrs.update({
            'class': 'form-control',
            'autocomplete': 'new-password',
            'placeholder': 'Enter a new password',
        })
        self.fields['new_password2'].widget.attrs.update({
            'class': 'form-control',
            'autocomplete': 'new-password',
            'placeholder': 'Confirm your new password',
        })


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ['name', 'email', 'phone', 'source', 'company']
        labels = {
            'name': 'Name',
            'email': 'Email address',
            'phone': 'Phone number',
            'source': 'Source',
            'company': 'Company',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'source': forms.Select(attrs={'class': 'form-select'}),
            'company': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['company'].queryset = Company.objects.filter(owner=user)

    def clean_company(self):
        company = self.cleaned_data.get('company')
        if company and company.owner_id != self.user.pk:
            raise ValidationError('Select one of your own companies.')
        return company

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        duplicates = Contact.objects.filter(owner=self.user, email__iexact=email)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError(
                'You already have a contact with this email address.'
            )
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '')
        if phone and not re.fullmatch(r'[0-9+\- ()]+', phone):
            raise ValidationError(
                'Enter a phone number using only digits, +, -, spaces, and brackets.'
            )
        return phone


class CompanyNameValidationMixin:
    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        duplicates = Company.objects.filter(
            owner=self.user,
            name__iexact=name,
        )
        instance = getattr(self, 'instance', None)
        if instance is not None and instance.pk:
            duplicates = duplicates.exclude(pk=instance.pk)
        if duplicates.exists():
            raise ValidationError(
                'You already have a company with this name.'
            )
        return name


class CompanyForm(CompanyNameValidationMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            'name',
            'website',
            'industry',
            'phone',
            'email',
            'address',
            'city',
            'country',
            'notes',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'industry': forms.Select(attrs={'class': 'form-select'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'country': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class CompanyQuickCreateForm(CompanyNameValidationMixin, forms.Form):
    name = forms.CharField(
        max_length=150,
        label='Company name',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class CompanyFilterForm(forms.Form):
    SORT_CHOICES = (
        ('name', 'Name A-Z'),
        ('newest', 'Newest'),
        ('contacts', 'Most contacts'),
    )
    q = forms.CharField(
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Name, website, city, or industry',
        }),
    )
    industry = forms.ChoiceField(
        required=False,
        label='Industry',
        choices=(('', 'All industries'), *Company.INDUSTRY_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    sort = forms.ChoiceField(
        required=False,
        label='Sort by',
        choices=SORT_CHOICES,
        initial='name',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class LeadForm(forms.ModelForm):
    estimated_value = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'step': '0.01',
        }),
    )

    class Meta:
        model = Lead
        fields = [
            'name', 'email', 'phone', 'company_name', 'job_title', 'source',
            'status', 'estimated_value', 'currency', 'notes',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),
            'job_title': forms.TextInput(attrs={'class': 'form-control'}),
            'source': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
        }

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if not name:
            raise ValidationError('Enter a lead name.')
        return name

    def clean_company_name(self):
        return self.cleaned_data.get('company_name', '').strip()

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '')
        if phone and not re.fullmatch(r'[0-9+\- ()]+', phone):
            raise ValidationError(
                'Enter a phone number using only digits, +, -, spaces, and brackets.'
            )
        return phone


class LeadFilterForm(forms.Form):
    SORT_CHOICES = (
        ('', 'Newest'),
        ('oldest', 'Oldest'),
        ('name', 'Name A-Z'),
        ('value_desc', 'Estimated value high-low'),
    )

    q = forms.CharField(
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Name, email, phone, or company',
        }),
    )
    status = forms.ChoiceField(
        required=False,
        choices=(('', 'All statuses'), *Lead.STATUS_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    source = forms.ChoiceField(
        required=False,
        choices=(('', 'All sources'), *LEAD_SOURCE_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    sort = forms.ChoiceField(
        required=False,
        choices=SORT_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class LeadConvertForm(forms.Form):
    create_deal = forms.BooleanField(required=False, initial=True)
    create_company = forms.BooleanField(required=False, initial=True)
    deal_title = forms.CharField(
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    deal_value = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'step': '0.01',
        }),
    )
    deal_stage = forms.ChoiceField(
        choices=Deal.STAGE_CHOICES,
        initial='new',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class DealForm(forms.ModelForm):
    value = forms.DecimalField(
        label='Deal value',
        min_value=0,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'step': '0.01',
        }),
    )

    class Meta:
        model = Deal
        fields = ['contact', 'title', 'stage', 'value', 'currency']
        labels = {
            'contact': 'Contact',
            'title': 'Deal title',
            'stage': 'Stage',
            'value': 'Deal value',
            'currency': 'Currency',
        }
        widgets = {
            'contact': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'stage': forms.Select(attrs={'class': 'form-select'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['contact'].queryset = Contact.objects.filter(owner=user)
        self.fields['title'].required = True

    def clean_contact(self):
        contact = self.cleaned_data['contact']
        if not Contact.objects.filter(pk=contact.pk, owner=self.user).exists():
            raise ValidationError('Select one of your own contacts.')
        return contact


class ContactFilterForm(forms.Form):
    SORT_CHOICES = (
        ('', 'Newest first'),
        ('oldest', 'Oldest first'),
        ('name_asc', 'Name A-Z'),
        ('name_desc', 'Name Z-A'),
    )

    q = forms.CharField(
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Name, email, phone, or company',
        }),
    )
    sort = forms.ChoiceField(
        required=False,
        label='Sort by',
        choices=SORT_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    source = forms.ChoiceField(
        required=False,
        label='Source',
        choices=(('', 'All sources'), *LEAD_SOURCE_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    company = forms.ModelChoiceField(
        required=False,
        label='Company',
        queryset=Company.objects.none(),
        empty_label='All companies',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['company'].queryset = Company.objects.filter(owner=user)


class DealFilterForm(forms.Form):
    SORT_CHOICES = (
        ('', 'Newest first'),
        ('oldest', 'Oldest first'),
        ('value_desc', 'Value high-low'),
        ('value_asc', 'Value low-high'),
        ('title_asc', 'Title A-Z'),
    )

    q = forms.CharField(
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Deal title or contact name',
        }),
    )
    stage = forms.ChoiceField(
        required=False,
        label='Stage',
        choices=(('', 'All stages'), *Deal.STAGE_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    currency = forms.ChoiceField(
        required=False,
        label='Currency',
        choices=(('', 'All currencies'), *Deal.CURRENCY_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    min_value = forms.DecimalField(
        required=False,
        min_value=0,
        label='Minimum value',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'step': '0.01',
            'placeholder': 'Minimum',
        }),
    )
    max_value = forms.DecimalField(
        required=False,
        min_value=0,
        label='Maximum value',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'step': '0.01',
            'placeholder': 'Maximum',
        }),
    )
    sort = forms.ChoiceField(
        required=False,
        label='Sort by',
        choices=SORT_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        min_value = cleaned_data.get('min_value')
        max_value = cleaned_data.get('max_value')
        if min_value is not None and max_value is not None and min_value > max_value:
            self.add_error(
                'min_value',
                'Minimum value must not exceed the maximum value.',
            )
            self.add_error('max_value', 'Maximum value must be at least the minimum value.')
        return cleaned_data


class DealBoardFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Deal title or contact name',
        }),
    )
    currency = forms.ChoiceField(
        required=False,
        label='Currency',
        choices=(('', 'All currencies'), *Deal.CURRENCY_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ['content']
        labels = {'content': 'Note'}
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Write a note...',
            }),
        }

    def __init__(self, *args, owner, contact, deal=None, **kwargs):
        self.owner = owner
        self.contact = contact
        self.deal = deal
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if self.is_bound and any(
            key in self.data
            for key in ('owner', 'owner_id', 'contact', 'contact_id', 'deal', 'deal_id')
        ):
            raise ValidationError(
                'Note ownership and links are determined by the current page.'
            )
        if self.contact.owner_id != self.owner.pk:
            raise ValidationError('The contact must belong to the note owner.')
        if self.deal is not None:
            if self.deal.contact_id != self.contact.pk:
                raise ValidationError('The deal must belong to the selected contact.')
            if self.deal.contact.owner_id != self.owner.pk:
                raise ValidationError('The deal must belong to the note owner.')
        return cleaned_data

    def save(self, commit=True):
        note = super().save(commit=False)
        note.owner = self.owner
        note.contact = self.contact
        note.deal = self.deal
        if commit:
            note.save()
        return note


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            'title',
            'description',
            'contact',
            'deal',
            'due_date',
            'priority',
            'status',
        ]
        labels = {
            'title': 'Task title',
            'description': 'Description',
            'contact': 'Contact',
            'deal': 'Deal',
            'due_date': 'Due date and time',
            'priority': 'Priority',
            'status': 'Status',
        }
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'contact': forms.Select(attrs={'class': 'form-select'}),
            'deal': forms.Select(attrs={'class': 'form-select'}),
            'due_date': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['contact'].queryset = Contact.objects.filter(owner=user)
        self.fields['contact'].required = False
        self.fields['deal'].queryset = Deal.objects.filter(contact__owner=user)
        self.fields['deal'].required = False
        self.fields['due_date'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean(self):
        cleaned_data = super().clean()
        contact = cleaned_data.get('contact')
        deal = cleaned_data.get('deal')
        if contact and contact.owner_id != self.user.pk:
            self.add_error('contact', 'Select one of your own contacts.')
        if deal:
            if deal.contact.owner_id != self.user.pk:
                self.add_error('deal', 'Select one of your own deals.')
            else:
                cleaned_data['contact'] = deal.contact
                self.instance.contact = deal.contact
        return cleaned_data

    def save(self, commit=True):
        task = super().save(commit=False)
        task.owner = self.user
        if task.deal_id:
            task.contact = task.deal.contact
        if task.status == 'completed' and task.completed_at is None:
            task.completed_at = timezone.now()
        elif task.status == 'pending':
            task.completed_at = None
        if commit:
            task.save()
        return task
