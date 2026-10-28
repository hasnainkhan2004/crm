from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.db.models.functions import Lower, Trim
from django.utils import timezone


CURRENCY_CHOICES = [
    ('PKR', 'PKR'),
    ('USD', 'USD'),
    ('AED', 'AED'),
    ('GBP', 'GBP'),
    ('EUR', 'EUR'),
]

LEAD_SOURCE_CHOICES = [
    ('website', 'Website'),
    ('facebook', 'Facebook'),
    ('instagram', 'Instagram'),
    ('whatsapp', 'WhatsApp'),
    ('referral', 'Referral'),
    ('cold_call', 'Cold Call'),
    ('event', 'Event'),
    ('other', 'Other'),
]


class Company(models.Model):
    INDUSTRY_CHOICES = [
        ('technology', 'Technology'),
        ('retail', 'Retail'),
        ('healthcare', 'Healthcare'),
        ('education', 'Education'),
        ('finance', 'Finance'),
        ('manufacturing', 'Manufacturing'),
        ('real_estate', 'Real Estate'),
        ('services', 'Services'),
        ('other', 'Other'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='companies',
    )
    name = models.CharField(max_length=150, db_index=True)
    website = models.URLField(blank=True)
    industry = models.CharField(
        max_length=30,
        choices=INDUSTRY_CHOICES,
        blank=True,
    )
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name', 'pk')
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('name')),
                F('owner'),
                name='uniq_company_owner_name_ci_trim',
            ),
        ]

    def __str__(self):
        return self.name


class Contact(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contacts',
        null=True,
    )
    name = models.CharField(max_length=100, db_index=True)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    source = models.CharField(
        max_length=20,
        choices=LEAD_SOURCE_CHOICES,
        blank=True,
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        related_name='contacts',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def clean(self):
        if (
            self.company_id
            and self.owner_id
            and self.company.owner_id != self.owner_id
        ):
            raise ValidationError({
                'company': 'The company must belong to the contact owner.',
            })

    def __str__(self):
        return self.name


class Lead(models.Model):
    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('unqualified', 'Unqualified'),
        ('converted', 'Converted'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='crm_leads',
    )
    name = models.CharField(max_length=100, db_index=True)
    email = models.EmailField(blank=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    company_name = models.CharField(max_length=150, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    source = models.CharField(
        max_length=20,
        choices=LEAD_SOURCE_CHOICES,
        default='website',
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='new',
        db_index=True,
    )
    estimated_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default='PKR',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_contact = models.ForeignKey(
        'Contact',
        on_delete=models.SET_NULL,
        related_name='source_lead',
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ('-created_at', '-pk')

    def __str__(self):
        return self.name


class Deal(models.Model):
    STAGE_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('negotiation', 'Negotiation'),
        ('won', 'Won'),
        ('lost', 'Lost'),
    ]
    CURRENCY_CHOICES = CURRENCY_CHOICES
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='deals')
    title = models.CharField(max_length=150, db_index=True)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='new', db_index=True)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='PKR', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    stage_changed_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"{self.title} ({self.contact.name})"


class DealStageHistory(models.Model):
    deal = models.ForeignKey(
        Deal,
        on_delete=models.CASCADE,
        related_name='stage_history',
    )
    from_stage = models.CharField(max_length=20, choices=Deal.STAGE_CHOICES)
    to_stage = models.CharField(max_length=20, choices=Deal.STAGE_CHOICES)
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='deal_stage_changes',
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ('-changed_at', '-pk')

    def __str__(self):
        return f"{self.deal}: {self.from_stage} to {self.to_stage}"


class Note(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='crm_notes',
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='notes',
    )
    deal = models.ForeignKey(
        Deal,
        on_delete=models.SET_NULL,
        related_name='notes',
        null=True,
        blank=True,
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at', '-pk')

    def clean(self):
        errors = {}
        if self.contact_id and self.owner_id:
            if self.contact.owner_id != self.owner_id:
                errors['contact'] = 'The contact must belong to the note owner.'
        if self.deal_id and self.contact_id:
            if self.deal.contact_id != self.contact_id:
                errors['deal'] = 'The deal must belong to the selected contact.'
            elif self.owner_id and self.deal.contact.owner_id != self.owner_id:
                errors['deal'] = 'The deal must belong to the note owner.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Note for {self.contact}"


class Task(models.Model):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='crm_tasks',
    )
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        related_name='tasks',
        null=True,
        blank=True,
    )
    deal = models.ForeignKey(
        Deal,
        on_delete=models.SET_NULL,
        related_name='tasks',
        null=True,
        blank=True,
    )
    due_date = models.DateTimeField(db_index=True)
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default='medium',
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('due_date', 'pk')

    @property
    def is_overdue(self):
        return self.status == 'pending' and self.due_date < timezone.now()

    def clean(self):
        errors = {}
        if self.contact_id and self.owner_id:
            if self.contact.owner_id != self.owner_id:
                errors['contact'] = 'The contact must belong to the task owner.'
        if self.deal_id and self.owner_id:
            if self.deal.contact.owner_id != self.owner_id:
                errors['deal'] = 'The deal must belong to the task owner.'
            elif self.contact_id and self.deal.contact_id != self.contact_id:
                errors['deal'] = 'The deal must belong to the selected contact.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title