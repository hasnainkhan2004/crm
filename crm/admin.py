from django.contrib import admin
from django.db import transaction
from .models import Company, Contact, Deal, DealStageHistory, Lead, Note, Task
from .services import move_deal

@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'owner', 'email', 'company_name', 'status', 'source',
        'estimated_value', 'currency', 'created_at',
    )
    list_filter = ('status', 'source', 'currency', 'created_at')
    search_fields = ('name', 'email', 'phone', 'company_name', 'owner__username')
    ordering = ('-created_at',)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'owner', 'phone', 'company', 'created_at')
    list_filter = ('owner', 'company', 'created_at')
    search_fields = ('name', 'email', 'company__name', 'owner__username')
    ordering = ('-created_at',)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'industry', 'city', 'country', 'created_at')
    list_filter = ('industry', 'country', 'created_at')
    search_fields = ('name', 'website', 'city', 'email', 'owner__username')
    ordering = ('name',)


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ('title', 'contact', 'stage', 'value', 'currency', 'created_at')
    list_filter = ('stage', 'currency', 'created_at')
    fields = ('contact', 'title', 'stage', 'value', 'currency')
    search_fields = ('title', 'contact__name', 'contact__email')
    ordering = ('-created_at',)

    def save_model(self, request, obj, form, change):
        requested_stage = obj.stage
        previous_stage = None
        if change:
            previous_stage = Deal.objects.get(pk=obj.pk).stage
            if requested_stage != previous_stage:
                obj.stage = previous_stage
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if previous_stage is not None and requested_stage != previous_stage:
                move_deal(obj, requested_stage, request.user)


@admin.register(DealStageHistory)
class DealStageHistoryAdmin(admin.ModelAdmin):
    list_display = ('deal', 'from_stage', 'to_stage', 'changed_at', 'changed_by')
    list_filter = ('from_stage', 'to_stage', 'changed_at')
    search_fields = ('deal__title', 'changed_by__username')
    ordering = ('-changed_at',)
    readonly_fields = ('deal', 'from_stage', 'to_stage', 'changed_at', 'changed_by')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'owner', 'contact', 'deal', 'created_at', 'updated_at')
    list_filter = ('owner', 'created_at')
    search_fields = ('content', 'contact__name', 'deal__title')
    ordering = ('-created_at',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'owner',
        'contact',
        'deal',
        'due_date',
        'priority',
        'status',
    )
    list_filter = ('status', 'priority', 'due_date')
    search_fields = ('title', 'description', 'contact__name', 'deal__title')
    ordering = ('due_date',)