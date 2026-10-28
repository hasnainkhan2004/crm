from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Sum,
    Value,
    When,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    Company,
    Contact,
    Deal,
    DealStageHistory,
    Lead,
    Note,
    Task,
)


OPEN_STAGES = ('new', 'contacted', 'negotiation')
TIMELINE_TYPES = {
    'system',
    'lead_converted',
    'note',
    'task_created',
    'task_completed',
    'deal_created',
    'stage_change',
}
TIMELINE_FILTERS = {
    'all': TIMELINE_TYPES,
    'notes': {'note'},
    'tasks': {'task_created', 'task_completed'},
    'deals': {'deal_created'},
    'stage_changes': {'stage_change'},
    'system': {'system', 'lead_converted'},
}


@dataclass
class LeadConversionResult:
    contact: Contact
    company: Company | None
    deal: Deal | None
    linked_existing_contact: bool


def get_lead_dashboard_metrics(user):
    totals = Lead.objects.filter(owner=user).aggregate(
        total=Count('pk'),
        new_count=Count('pk', filter=Q(status='new')),
        qualified_count=Count('pk', filter=Q(status='qualified')),
        converted_count=Count('pk', filter=Q(status='converted')),
    )
    total = totals['total']
    converted = totals['converted_count']
    return {
        'new_leads': totals['new_count'],
        'qualified_leads': totals['qualified_count'],
        'converted_leads': converted,
        'conversion_rate': (
            round(Decimal(converted) * Decimal('100') / total, 2)
            if total else Decimal('0')
        ),
    }


def convert_lead(
    lead,
    user,
    create_deal=True,
    deal_title=None,
    deal_value=None,
    deal_stage='new',
    company_option=True,
):
    valid_stages = {value for value, _ in Deal.STAGE_CHOICES}
    if not isinstance(deal_stage, str) or deal_stage not in valid_stages:
        raise ValidationError({'deal_stage': 'Select a valid deal stage.'})

    with transaction.atomic():
        try:
            locked_lead = Lead.objects.select_for_update().select_related(
                'converted_contact',
            ).get(pk=lead.pk)
        except Lead.DoesNotExist:
            raise ValidationError({'lead': 'This lead could not be found.'})

        if locked_lead.owner_id != user.pk:
            raise ValidationError({'lead': 'You can only convert your own leads.'})
        if locked_lead.status == 'converted':
            raise ValidationError({'lead': 'This lead has already been converted.'})

        email = locked_lead.email.strip()
        contact = None
        linked_existing_contact = False
        if email:
            contact = Contact.objects.filter(
                owner=user,
                email__iexact=email,
            ).first()
        if contact is None:
            contact = Contact.objects.create(
                owner=user,
                name=locked_lead.name,
                email=email,
                phone=locked_lead.phone,
                source=locked_lead.source,
            )
        else:
            linked_existing_contact = True

        company = None
        company_name = locked_lead.company_name.strip()
        if company_name and company_option:
            company = Company.objects.filter(
                owner=user,
                name__iexact=company_name,
            ).order_by('pk').first()
            if company is None:
                company = Company.objects.create(
                    owner=user,
                    name=company_name,
                )
            if contact.company_id != company.pk:
                contact.company = company
                contact.save(update_fields=['company'])
        elif contact.company_id:
            company = contact.company

        deal = None
        if create_deal:
            resolved_title = (deal_title or '').strip() or f'{locked_lead.name} deal'
            resolved_value = (
                deal_value if deal_value is not None
                else locked_lead.estimated_value or Decimal('0')
            )
            deal = Deal.objects.create(
                contact=contact,
                title=resolved_title,
                value=resolved_value,
                currency=locked_lead.currency,
                stage=deal_stage,
            )
            DealStageHistory.objects.create(
                deal=deal,
                from_stage=deal_stage,
                to_stage=deal_stage,
                changed_at=deal.stage_changed_at,
                changed_by=user,
            )

        notes = locked_lead.notes.strip()
        if notes:
            Note.objects.create(
                owner=user,
                contact=contact,
                deal=deal,
                content=notes,
            )

        converted_at = timezone.now()
        Lead.objects.filter(pk=locked_lead.pk).update(
            status='converted',
            converted_at=converted_at,
            converted_contact=contact,
        )
        locked_lead.status = 'converted'
        locked_lead.converted_at = converted_at
        locked_lead.converted_contact = contact

    lead.status = locked_lead.status
    lead.converted_at = locked_lead.converted_at
    lead.converted_contact = contact
    lead.converted_contact_id = contact.pk
    return LeadConversionResult(contact, company, deal, linked_existing_contact)


def _timeline_limit(limit):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    return max(1, min(limit, 50))


def _timeline_before(before):
    if before is None:
        return None
    parsed = parse_datetime(str(before))
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _timeline_event_types(event_types):
    if not event_types:
        return set(TIMELINE_TYPES)
    values = {str(item).strip() for item in event_types if str(item).strip()}
    selected = set()
    for value in values:
        if value in TIMELINE_FILTERS:
            selected.update(TIMELINE_FILTERS[value])
        elif value in TIMELINE_TYPES:
            selected.add(value)
    return selected or set(TIMELINE_TYPES)


def _event_date_group(timestamp):
    today = timezone.localdate()
    event_day = timezone.localtime(timestamp).date()
    if event_day == today:
        return 'Today'
    if event_day == today - timedelta(days=1):
        return 'Yesterday'
    if event_day >= today - timedelta(days=today.weekday()):
        return 'This week'
    return event_day.strftime('%B %Y')


def _timeline_event(
    event_type,
    timestamp,
    title,
    description,
    icon,
    color,
    url,
    related,
):
    return {
        'type': event_type,
        'timestamp': timestamp,
        'title': title,
        'description': description,
        'icon': icon,
        'color': color,
        'url': url,
        'related': related,
        'date_group': _event_date_group(timestamp),
    }


def _contact_contact_events(contact, before, source_limit):
    events = []
    contact_created = contact.created_at
    if before is None or contact_created < before:
        events.append(_timeline_event(
            'system', contact_created, 'Contact created',
            f'{contact.name} was added to the CRM.', 'person-plus', 'secondary',
            reverse('contact_detail', args=[contact.pk]),
            {'contact': contact},
        ))

    leads = Lead.objects.filter(
        owner=contact.owner,
        converted_contact=contact,
        converted_at__isnull=False,
    ).select_related('converted_contact')
    if before is not None:
        leads = leads.filter(converted_at__lt=before)
    for lead in leads.order_by('-converted_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'lead_converted', lead.converted_at,
            f'Converted from lead: {lead.name}',
            f'Source: {lead.get_source_display()}', 'arrow-left-right', 'success',
            reverse('lead_detail', args=[lead.pk]),
            {'lead': lead},
        ))
    return events


def _contact_notes_events(contact, before, source_limit):
    notes = Note.objects.filter(owner=contact.owner, contact=contact).select_related(
        'deal',
    )
    if before is not None:
        notes = notes.filter(created_at__lt=before)
    events = []
    for note in notes.order_by('-created_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'note', note.created_at, 'Note added',
            note.content[:180] + ('...' if len(note.content) > 180 else ''),
            'journal-text', 'primary',
            reverse('note_edit', args=[note.pk]),
            {'note': note},
        ))
    return events


def _task_events(contact, before, source_limit):
    tasks = Task.objects.filter(owner=contact.owner, contact=contact)
    if before is not None:
        tasks = tasks.filter(
            Q(created_at__lt=before)
            | Q(completed_at__isnull=False, completed_at__lt=before)
        )
    tasks = list(tasks.order_by('-created_at', '-pk')[:source_limit])
    events = []
    for task in tasks:
        events.append(_timeline_event(
            'task_created', task.created_at, f'Task created: {task.title}',
            f'{task.get_priority_display()} priority · Due {timezone.localtime(task.due_date).strftime("%d %b %Y")}',
            'check2-square', 'warning', reverse('task_edit', args=[task.pk]),
            {'task': task},
        ))
        if task.completed_at is not None and (before is None or task.completed_at < before):
            events.append(_timeline_event(
                'task_completed', task.completed_at, f'Task completed: {task.title}',
                f'Due {timezone.localtime(task.due_date).strftime("%d %b %Y")}',
                'check-circle', 'success', reverse('task_edit', args=[task.pk]),
                {'task': task},
            ))
    return events


def _deal_events(contact, before, source_limit):
    deals = Deal.objects.filter(contact=contact, contact__owner=contact.owner)
    if before is not None:
        deals = deals.filter(created_at__lt=before)
    events = []
    for deal in deals.order_by('-created_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'deal_created', deal.created_at, f'Deal created: {deal.title}',
            f'{deal.value:,.2f} {deal.currency} · {deal.get_stage_display()} stage',
            'briefcase', 'info', reverse('deal_detail', args=[deal.pk]),
            {'deal': deal},
        ))
    return events


def _stage_history_events(contact, before, source_limit):
    history = DealStageHistory.objects.filter(
        deal__contact=contact,
        deal__contact__owner=contact.owner,
    ).select_related('deal', 'changed_by')
    if before is not None:
        history = history.filter(changed_at__lt=before)
    events = []
    for change in history.order_by('-changed_at', '-pk')[:source_limit]:
        changer = change.changed_by.get_username() if change.changed_by else 'Unknown user'
        events.append(_timeline_event(
            'stage_change', change.changed_at,
            f'{change.deal.title}: {change.get_from_stage_display()} → {change.get_to_stage_display()}',
            f'Changed by {changer}', 'shuffle', 'danger',
            reverse('deal_detail', args=[change.deal_id]),
            {'history': change},
        ))
    return events


def _collect_timeline(contact, user, event_types=None, limit=20, before=None):
    if contact.owner_id != user.pk:
        raise ValidationError('You can only view activity for your own contacts.')
    limit = _timeline_limit(limit)
    before = _timeline_before(before)
    selected = _timeline_event_types(event_types)
    source_limit = limit + 1
    events = []

    if 'system' in selected or 'lead_converted' in selected:
        events.extend(_contact_contact_events(contact, before, source_limit))
    if 'note' in selected:
        events.extend(_contact_notes_events(contact, before, source_limit))
    if selected.intersection({'task_created', 'task_completed'}):
        events.extend(_task_events(contact, before, source_limit))
    if 'deal_created' in selected:
        events.extend(_deal_events(contact, before, source_limit))
    if 'stage_change' in selected:
        events.extend(_stage_history_events(contact, before, source_limit))

    events.sort(key=lambda event: (event['timestamp'], event['title']), reverse=True)
    page = events[:limit]
    cursor = page[-1]['timestamp'].isoformat() if len(page) == limit else None
    has_more = len(events) > limit
    return page, cursor, has_more


def get_contact_timeline(contact, user, event_types=None, limit=20, before=None):
    events, cursor, has_more = _collect_timeline(
        contact, user, event_types=event_types, limit=limit, before=before,
    )
    return {'events': events, 'next_cursor': cursor, 'has_more': has_more}


def get_contact_summary(contact, user):
    if contact.owner_id != user.pk:
        raise ValidationError('You can only view activity for your own contacts.')
    deals = Deal.objects.filter(contact=contact)
    deal_totals = list(deals.values('currency').annotate(
        deal_count=Count('pk'),
        open_pipeline=Sum('value', filter=Q(stage__in=OPEN_STAGES)),
        won_revenue=Sum('value', filter=Q(stage='won')),
    ).order_by('currency'))
    note_count = Note.objects.filter(owner=user, contact=contact).count()
    tasks = Task.objects.filter(owner=user, contact=contact)
    pending = tasks.filter(status='pending')
    overdue = pending.filter(due_date__lt=timezone.now()).count()
    last_contact = Note.objects.filter(owner=user, contact=contact).aggregate(
        last=Max('created_at'),
    )['last']
    task_last = tasks.filter(status='completed').aggregate(
        last=Max('completed_at'),
    )['last']
    if task_last and (last_contact is None or task_last > last_contact):
        last_contact = task_last
    now = timezone.now()
    days_since = max(0, (now - last_contact).days) if last_contact else None
    return {
        'deal_totals': deal_totals,
        'total_deals': sum(row['deal_count'] for row in deal_totals),
        'open_pipeline': [
            {'currency': row['currency'], 'value': row['open_pipeline']}
            for row in deal_totals if row['open_pipeline'] is not None
        ],
        'won_revenue': [
            {'currency': row['currency'], 'value': row['won_revenue']}
            for row in deal_totals if row['won_revenue'] is not None
        ],
        'notes_count': note_count,
        'pending_tasks': pending.count(),
        'overdue_tasks': overdue,
        'last_activity': last_contact,
        'days_since_last_contact': days_since,
    }


def get_deal_timeline(deal, user, limit=20, before=None):
    if deal.contact.owner_id != user.pk:
        raise ValidationError('You can only view activity for your own deals.')
    limit = _timeline_limit(limit)
    before = _timeline_before(before)
    source_limit = limit + 1
    events = []
    if before is None or deal.created_at < before:
        events.append(_timeline_event(
            'deal_created', deal.created_at, f'Deal created: {deal.title}',
            f'{deal.value:,.2f} {deal.currency} · {deal.get_stage_display()} stage',
            'briefcase', 'info', reverse('deal_detail', args=[deal.pk]),
            {'deal': deal},
        ))
    history = DealStageHistory.objects.filter(deal=deal).select_related('changed_by')
    notes = Note.objects.filter(owner=user, deal=deal).select_related('contact')
    tasks = Task.objects.filter(owner=user, deal=deal).select_related('contact')
    if before is not None:
        history = history.filter(changed_at__lt=before)
        notes = notes.filter(created_at__lt=before)
        tasks = tasks.filter(
            Q(created_at__lt=before)
            | Q(completed_at__isnull=False, completed_at__lt=before)
        )
    for change in history.order_by('-changed_at', '-pk')[:source_limit]:
        changer = change.changed_by.get_username() if change.changed_by else 'Unknown user'
        events.append(_timeline_event(
            'stage_change', change.changed_at,
            f'{change.get_from_stage_display()} → {change.get_to_stage_display()}',
            f'Changed by {changer}', 'shuffle', 'danger',
            reverse('deal_detail', args=[deal.pk]), {'history': change},
        ))
    for note in notes.order_by('-created_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'note', note.created_at, 'Note added', note.content[:180],
            'journal-text', 'primary', reverse('note_edit', args=[note.pk]),
            {'note': note},
        ))
    for task in tasks.order_by('-created_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'task_created', task.created_at, f'Task created: {task.title}',
            f'{task.get_priority_display()} priority', 'check2-square', 'warning',
            reverse('task_edit', args=[task.pk]), {'task': task},
        ))
        if task.completed_at is not None and (before is None or task.completed_at < before):
            events.append(_timeline_event(
                'task_completed', task.completed_at, f'Task completed: {task.title}',
                'Task completed', 'check-circle', 'success',
                reverse('task_edit', args=[task.pk]), {'task': task},
            ))
    events.sort(key=lambda event: (event['timestamp'], event['title']), reverse=True)
    page = events[:limit]
    return {
        'events': page,
        'next_cursor': page[-1]['timestamp'].isoformat() if len(page) == limit else None,
        'has_more': len(events) > limit,
    }


def get_recent_contact_activity(user, limit=8):
    limit = _timeline_limit(limit)
    # Each source remains bounded and filtered through the owned contact relation.
    source_limit = limit + 1
    events = []
    deals = Deal.objects.filter(contact__owner=user).select_related('contact')
    notes = Note.objects.filter(owner=user).select_related('contact', 'deal')
    tasks = Task.objects.filter(owner=user).select_related('contact')
    history = DealStageHistory.objects.filter(deal__contact__owner=user).select_related(
        'deal', 'deal__contact', 'changed_by',
    )
    leads = Lead.objects.filter(
        owner=user,
        converted_contact__owner=user,
        converted_at__isnull=False,
    ).select_related('converted_contact')
    for contact in Contact.objects.filter(owner=user).only(
        'id', 'name', 'created_at',
    ).order_by('-created_at', '-pk')[:source_limit]:
        if contact.created_at:
            events.append(_timeline_event(
                'system', contact.created_at, f'{contact.name} created',
                'Contact created', 'person-plus', 'secondary',
                reverse('contact_detail', args=[contact.pk]),
                {'contact': contact},
            ))
    for note in notes.order_by('-created_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'note', note.created_at, f'Note added for {note.contact.name}',
            note.content[:180], 'journal-text', 'primary',
            reverse('contact_detail', args=[note.contact_id]), {'note': note},
        ))
    for task in tasks.order_by('-created_at', '-pk')[:source_limit]:
        if task.contact_id:
            events.append(_timeline_event(
                'task_created', task.created_at, f'Task created: {task.title}',
                f'{task.get_priority_display()} priority', 'check2-square', 'warning',
                reverse('contact_detail', args=[task.contact_id]), {'task': task},
            ))
            if task.completed_at:
                events.append(_timeline_event(
                    'task_completed', task.completed_at, f'Task completed: {task.title}',
                    f'{task.get_priority_display()} priority', 'check-circle', 'success',
                    reverse('contact_detail', args=[task.contact_id]), {'task': task},
                ))
    for deal in deals.order_by('-created_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'deal_created', deal.created_at, f'Deal created: {deal.title}',
            f'{deal.value:,.2f} {deal.currency}', 'briefcase', 'info',
            reverse('contact_detail', args=[deal.contact_id]), {'deal': deal},
        ))
    for lead in leads.order_by('-converted_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'lead_converted', lead.converted_at,
            f'{lead.converted_contact.name} converted from {lead.name}',
            f'Source: {lead.get_source_display()}', 'arrow-left-right', 'success',
            reverse('contact_detail', args=[lead.converted_contact_id]),
            {'lead': lead, 'contact': lead.converted_contact},
        ))
    for change in history.order_by('-changed_at', '-pk')[:source_limit]:
        events.append(_timeline_event(
            'stage_change', change.changed_at,
            f'{change.deal.title}: {change.get_from_stage_display()} → {change.get_to_stage_display()}',
            f'Changed by {change.changed_by.get_username() if change.changed_by else "Unknown user"}',
            'shuffle', 'danger', reverse('contact_detail', args=[change.deal.contact_id]),
            {'history': change},
        ))
    events.sort(key=lambda event: (event['timestamp'], event['title']), reverse=True)
    return {'events': events[:limit]}


def calculate_sales_kpis(user):
    currency_totals = (
        Deal.objects.filter(contact__owner=user)
        .values('currency')
        .annotate(
            open_pipeline=Sum('value', filter=Q(stage__in=OPEN_STAGES)),
            won_revenue=Sum('value', filter=Q(stage='won')),
            lost_value=Sum('value', filter=Q(stage='lost')),
            won_count=Count('id', filter=Q(stage='won')),
            lost_count=Count('id', filter=Q(stage='lost')),
        )
        .order_by('currency')
    )

    open_pipeline_values = []
    won_revenue_values = []
    lost_values = []
    won_count = 0
    lost_count = 0

    for totals in currency_totals:
        currency = totals['currency']
        if totals['open_pipeline'] is not None:
            open_pipeline_values.append({
                'currency': currency,
                'value': totals['open_pipeline'],
            })
        if totals['won_revenue'] is not None:
            won_revenue_values.append({
                'currency': currency,
                'value': totals['won_revenue'],
            })
        if totals['lost_value'] is not None:
            lost_values.append({
                'currency': currency,
                'value': totals['lost_value'],
            })
        won_count += totals['won_count']
        lost_count += totals['lost_count']

    closed_count = won_count + lost_count
    win_rate = (
        round(Decimal(won_count) * Decimal('100') / closed_count, 2)
        if closed_count
        else Decimal('0')
    )

    return {
        'open_pipeline_values': open_pipeline_values,
        'won_revenue_values': won_revenue_values,
        'lost_values': lost_values,
        'win_rate': win_rate,
    }


def move_deal(deal, new_stage, user):
    valid_stages = {value for value, _ in Deal.STAGE_CHOICES}
    if not isinstance(new_stage, str) or new_stage not in valid_stages:
        raise ValidationError({'stage': 'Select a valid deal stage.'})

    with transaction.atomic():
        current = Deal.objects.select_for_update().select_related(
            'contact',
        ).get(pk=deal.pk)
        previous_stage = current.stage
        if previous_stage == new_stage:
            return previous_stage

        changed_at = timezone.now()
        Deal.objects.filter(pk=current.pk).update(
            stage=new_stage,
            stage_changed_at=changed_at,
        )
        DealStageHistory.objects.create(
            deal=current,
            from_stage=previous_stage,
            to_stage=new_stage,
            changed_at=changed_at,
            changed_by=user,
        )

    deal.stage = new_stage
    deal.stage_changed_at = changed_at
    return previous_stage


def get_deal_stage_totals(user, stages=None, query='', currency=''):
    queryset = Deal.objects.filter(contact__owner=user)
    if stages is not None:
        queryset = queryset.filter(stage__in=stages)
    query = query.strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(contact__name__icontains=query)
        )
    if currency:
        queryset = queryset.filter(currency=currency)
    return list(
        queryset.values('stage', 'currency')
        .annotate(count=Count('pk'), total=Sum('value'))
        .order_by('stage', 'currency')
    )


def build_deal_board(user, query='', currency=''):
    stage_order = [value for value, _ in Deal.STAGE_CHOICES]
    overdue_tasks = Task.objects.filter(
        deal_id=OuterRef('pk'),
        owner=user,
        status='pending',
        due_date__lt=timezone.now(),
    )
    deals = Deal.objects.filter(
        contact__owner=user,
    ).select_related('contact', 'contact__company').annotate(
        has_overdue_task=Exists(overdue_tasks),
        stage_order=Case(
            *[
                When(stage=value, then=Value(index))
                for index, value in enumerate(stage_order)
            ],
            output_field=IntegerField(),
        ),
    )
    query = query.strip()
    if query:
        deals = deals.filter(
            Q(title__icontains=query)
            | Q(contact__name__icontains=query)
        )
    if currency:
        deals = deals.filter(currency=currency)
    deals = list(deals.order_by('stage_order', '-stage_changed_at', '-pk'))

    totals_by_stage = {}
    for row in get_deal_stage_totals(
        user,
        stages=stage_order,
        query=query,
        currency=currency,
    ):
        totals_by_stage.setdefault(row['stage'], []).append(row)

    deals_by_stage = {stage: [] for stage in stage_order}
    for deal in deals:
        deals_by_stage[deal.stage].append(deal)

    columns = []
    for value, label in Deal.STAGE_CHOICES:
        stage_totals = totals_by_stage.get(value, [])
        columns.append({
            'value': value,
            'label': label,
            'deals': deals_by_stage[value],
            'count': sum(row['count'] for row in stage_totals),
            'totals': [
                {'currency': row['currency'], 'value': row['total']}
                for row in stage_totals
            ],
        })
    return columns


def get_company_deal_summary(company, user):
    return list(
        Deal.objects.filter(
            contact__company=company,
            contact__owner=user,
        )
        .values('currency')
        .annotate(
            deal_count=Count('pk'),
            open_pipeline=Sum(
                'value',
                filter=Q(stage__in=OPEN_STAGES),
            ),
            won_revenue=Sum('value', filter=Q(stage='won')),
        )
        .order_by('currency')
    )


def local_day_bounds():
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min))
    end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    return start, end


def get_dashboard_tasks(user, limit=5):
    _, day_end = local_day_bounds()
    return (
        Task.objects.filter(
            owner=user,
            status='pending',
            due_date__lt=day_end,
        )
        .select_related('contact', 'deal')
        .order_by('due_date', 'pk')[:limit]
    )


def get_task_attention_count(user):
    if not user.is_authenticated:
        return 0
    now = timezone.now()
    day_start, day_end = local_day_bounds()
    return Task.objects.filter(
        owner=user,
        status='pending',
    ).filter(
        Q(due_date__lt=now)
        | Q(due_date__gte=day_start, due_date__lt=day_end)
    ).count()
