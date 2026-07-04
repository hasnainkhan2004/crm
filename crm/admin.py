from django.contrib import admin
from .models import Contact, Deal

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'company', 'created_at')
    search_fields = ('name', 'email', 'company')


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ('title', 'contact', 'stage', 'value', 'created_at')
    list_filter = ('stage',)
    search_fields = ('title',)