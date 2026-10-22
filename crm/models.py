from django.conf import settings
from django.db import models

class Contact(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contacts',
        null=True,
    )
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    company = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='deals')
    title = models.CharField(max_length=150)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='new')
    value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.contact.name})"


class Activity(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='activities')
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.contact.name} - {self.created_at.strftime('%d %b %Y')}"