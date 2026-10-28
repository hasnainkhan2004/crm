import json
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from .models import Company, Contact, Deal, Lead, Note, Task
from .services import (
    build_deal_board,
    calculate_sales_kpis,
    convert_lead,
    get_contact_summary,
    get_contact_timeline,
    get_deal_timeline,
    get_lead_dashboard_metrics,
    get_recent_contact_activity,
    get_deal_stage_totals,
    get_dashboard_tasks,
    get_company_deal_summary,
    local_day_bounds,
    move_deal,
)
from .forms import (
    CompanyFilterForm,
    CompanyForm,
    CompanyQuickCreateForm,
    ContactForm,
    ContactFilterForm,
    DealBoardFilterForm,
    DealForm,
    DealFilterForm,
    LeadConvertForm,
    LeadFilterForm,
    LeadForm,
    NoteForm,
    SignUpForm,
    TaskForm,
)
from .templatetags.crm_extras import display_currency, stage_badge_class
from django.views.decorators.http import require_POST


def signup(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('dashboard')
    else:
        form = SignUpForm()
    return render(request, 'crm/signup.html', {'form': form})


@login_required(login_url='login')
def dashboard(request):
    contacts = Contact.objects.filter(owner=request.user)
    deals = Deal.objects.filter(contact__owner=request.user)
    total_contacts = contacts.count()
    total_deals = deals.count()
    sales_kpis = calculate_sales_kpis(request.user)
    lead_metrics = get_lead_dashboard_metrics(request.user)
    stage_counts = deals.values('stage').annotate(count=Count('id'))
    stage_count_by_value = {
        item['stage']: item['count'] for item in stage_counts
    }
    stage_rows = [
        {
            'value': value,
            'label': label,
            'count': stage_count_by_value.get(value, 0),
        }
        for value, label in Deal.STAGE_CHOICES
    ]

    context = {
        'total_contacts': total_contacts,
        'total_deals': total_deals,
        **sales_kpis,
        **lead_metrics,
        'recent_activity': get_recent_contact_activity(request.user)['events'],
        'dashboard_tasks': get_dashboard_tasks(request.user),
        'stage_rows': stage_rows,
        'chart_labels': json.dumps([row['label'] for row in stage_rows]),
        'chart_data': json.dumps([row['count'] for row in stage_rows]),
    }
    return render(request, 'crm/dashboard.html', context)


class ContactListView(LoginRequiredMixin, ListView):
    login_url = 'login'
    model = Contact
    template_name = 'crm/contact_list.html'
    context_object_name = 'contacts'
    paginate_by = 10

    def get_queryset(self):
        self.filter_form = ContactFilterForm(self.request.GET, user=self.request.user)
        self.filter_form.is_valid()
        filters = self.filter_form.cleaned_data
        queryset = Contact.objects.filter(owner=self.request.user).select_related(
            'company',
        )

        query = filters.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
                | Q(company__name__icontains=query)
            )
        if filters.get('company'):
            queryset = queryset.filter(company=filters['company'])
        if filters.get('source'):
            queryset = queryset.filter(source=filters['source'])

        sort_fields = {
            '': ('-created_at', '-pk'),
            'oldest': ('created_at', 'pk'),
            'name_asc': ('name', 'pk'),
            'name_desc': ('-name', '-pk'),
        }
        return queryset.order_by(
            *sort_fields.get(filters.get('sort'), ('-created_at',))
        )

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get(self.page_kwarg))
        return paginator, page, page.object_list, paginator.num_pages > 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.filter_form.cleaned_data
        context.update({
            'filter_form': self.filter_form,
            'has_any_contacts': Contact.objects.filter(owner=self.request.user).exists(),
            'has_filters': (
                bool(filters.get('q', '').strip())
                or bool(filters.get('company'))
                or bool(filters.get('source'))
            ),
        })
        return context


class ContactCreateView(LoginRequiredMixin, CreateView):
    login_url = 'login'
    model = Contact
    form_class = ContactForm
    template_name = 'crm/contact_form.html'
    success_url = reverse_lazy('contact_list')
    extra_context = {'title': 'Add Contact'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        company_id = self.request.GET.get('company', '')
        if company_id.isdecimal():
            company = Company.objects.filter(
                pk=int(company_id),
                owner=self.request.user,
            ).first()
            if company:
                initial['company'] = company.pk
        return initial

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, 'Contact created successfully.')
        return response


class ContactUpdateView(LoginRequiredMixin, UpdateView):
    login_url = 'login'
    model = Contact
    form_class = ContactForm
    template_name = 'crm/contact_form.html'
    success_url = reverse_lazy('contact_list')
    extra_context = {'title': 'Edit Contact'}

    def get_queryset(self):
        return Contact.objects.filter(owner=self.request.user).select_related(
            'company',
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Contact updated successfully.')
        return response


class ContactDetailView(LoginRequiredMixin, DetailView):
    login_url = 'login'
    model = Contact
    template_name = 'crm/contact_detail.html'
    context_object_name = 'contact'

    def get_queryset(self):
        return Contact.objects.filter(owner=self.request.user).select_related(
            'company',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_type = self.request.GET.get('type', 'all')
        context['source_lead'] = self.object.source_lead.filter(
            owner=self.request.user,
        ).order_by('-converted_at', '-pk').first()
        context['deals'] = self.object.deals.select_related(
            'contact',
            'contact__company',
        ).order_by('-created_at', '-pk')
        context['contact_tasks'] = (
            Task.objects.filter(
                owner=self.request.user,
                contact=self.object,
                status='pending',
            )
            .select_related('deal')
            .order_by('due_date', 'pk')
        )
        context['contact_task_add_url'] = (
            f"{reverse('task_add')}?{urlencode({
                'contact': self.object.pk,
                'next': reverse('contact_detail', args=[self.object.pk]),
            })}"
        )
        context.update(contact_note_context(
            self.object,
            self.request.user,
            context.get('note_form'),
        ))
        context['summary'] = get_contact_summary(self.object, self.request.user)
        context['timeline'] = get_contact_timeline(
            self.object,
            self.request.user,
            event_types=[event_type],
            limit=20,
        )
        context['timeline_type'] = event_type if event_type in {
            'all', 'notes', 'tasks', 'deals', 'stage_changes', 'system',
        } else 'all'
        context['timeline_filter_labels'] = [
            ('all', 'All'),
            ('notes', 'Notes'),
            ('tasks', 'Tasks'),
            ('deals', 'Deals'),
            ('stage_changes', 'Stage changes'),
            ('system', 'System'),
        ]
        return context

@login_required(login_url='login')
def contact_timeline(request, pk):
    contact = get_object_or_404(Contact, pk=pk, owner=request.user)
    event_type = request.GET.get('type', 'all')
    if event_type not in {'all', 'notes', 'tasks', 'deals', 'stage_changes', 'system'}:
        event_type = 'all'
    timeline = get_contact_timeline(
        contact,
        request.user,
        event_types=[event_type],
        limit=request.GET.get('limit', '20'),
        before=request.GET.get('before'),
    )
    return render(request, 'crm/includes/timeline_fragment.html', {
        'timeline': timeline,
        'timeline_type': event_type,
        'contact': contact,
    })


def contact_note_context(contact, user, note_form=None):
    notes = Note.objects.filter(
        owner=user,
        contact=contact,
    ).select_related('deal')
    return {
        'notes': notes,
        'notes_count': notes.count(),
        'note_form': note_form or NoteForm(
            owner=user,
            contact=contact,
        ),
        'note_add_url': reverse('contact_note_add', args=[contact.pk]),
    }


def deal_note_context(deal, user, note_form=None):
    notes = Note.objects.filter(
        owner=user,
        contact=deal.contact,
        deal=deal,
    ).select_related('deal')
    return {
        'notes': notes,
        'notes_count': notes.count(),
        'note_form': note_form or NoteForm(
            owner=user,
            contact=deal.contact,
            deal=deal,
        ),
        'note_add_url': reverse('deal_note_add', args=[deal.pk]),
    }


@login_required(login_url='login')
def contact_note_add(request, pk):
    contact = get_object_or_404(Contact, pk=pk, owner=request.user)
    if request.method != 'POST':
        return redirect('contact_detail', pk=contact.pk)

    form = NoteForm(
        request.POST,
        owner=request.user,
        contact=contact,
    )
    if form.is_valid():
        form.save()
        messages.success(request, 'Note added successfully.')
        return redirect('contact_detail', pk=contact.pk)

    context = {
        'contact': contact,
        'deals': contact.deals.all(),
        **contact_note_context(contact, request.user, form),
    }
    return render(request, 'crm/contact_detail.html', context)


@login_required(login_url='login')
def deal_note_add(request, pk):
    deal = get_object_or_404(
        Deal.objects.select_related('contact'),
        pk=pk,
        contact__owner=request.user,
    )
    if request.method != 'POST':
        return redirect('deal_detail', pk=deal.pk)

    form = NoteForm(
        request.POST,
        owner=request.user,
        contact=deal.contact,
        deal=deal,
    )
    if form.is_valid():
        form.save()
        messages.success(request, 'Note added successfully.')
        return redirect('deal_detail', pk=deal.pk)

    return render(request, 'crm/deal_detail.html', {
        'deal': deal,
        **deal_note_context(deal, request.user, form),
    })


def note_return_url(request, note):
    requested_url = request.POST.get('next') or request.GET.get('next')
    allowed_urls = {
        reverse('contact_detail', args=[note.contact_id]),
    }
    if note.deal_id:
        allowed_urls.add(reverse('deal_detail', args=[note.deal_id]))
    if requested_url in allowed_urls:
        return requested_url
    return reverse(
        'deal_detail' if note.deal_id else 'contact_detail',
        args=[note.deal_id if note.deal_id else note.contact_id],
    )


class NoteUpdateView(LoginRequiredMixin, UpdateView):
    login_url = 'login'
    model = Note
    form_class = NoteForm
    template_name = 'crm/note_form.html'
    context_object_name = 'note'

    def get_queryset(self):
        return Note.objects.filter(owner=self.request.user).select_related(
            'contact',
            'deal',
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({
            'owner': self.request.user,
            'contact': self.object.contact,
            'deal': self.object.deal,
        })
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['return_url'] = note_return_url(self.request, self.object)
        return context

    def get_success_url(self):
        return note_return_url(self.request, self.object)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Note updated successfully.')
        return response


class NoteDeleteView(LoginRequiredMixin, DeleteView):
    login_url = 'login'
    model = Note
    template_name = 'crm/note_confirm_delete.html'
    context_object_name = 'note'

    def get_queryset(self):
        return Note.objects.filter(owner=self.request.user).select_related(
            'contact',
            'deal',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['return_url'] = note_return_url(self.request, self.object)
        return context

    def form_valid(self, form):
        self.success_url = note_return_url(self.request, self.object)
        response = super().form_valid(form)
        messages.success(self.request, 'Note deleted successfully.')
        return response


class ContactDeleteView(LoginRequiredMixin, DeleteView):
    login_url = 'login'
    model = Contact
    template_name = 'crm/contact_confirm_delete.html'
    context_object_name = 'contact'
    success_url = reverse_lazy('contact_list')

    def get_queryset(self):
        return Contact.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['deal_count'] = self.object.deals.count()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Contact deleted successfully.')
        return response


class LeadListView(LoginRequiredMixin, ListView):
    login_url = 'login'
    model = Lead
    template_name = 'crm/lead_list.html'
    context_object_name = 'leads'
    paginate_by = 10

    def get_queryset(self):
        self.filter_form = LeadFilterForm(self.request.GET)
        self.filter_form.is_valid()
        filters = self.filter_form.cleaned_data
        queryset = Lead.objects.filter(owner=self.request.user)

        query = filters.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
                | Q(company_name__icontains=query)
            )
        if filters.get('status'):
            queryset = queryset.filter(status=filters['status'])
        if filters.get('source'):
            queryset = queryset.filter(source=filters['source'])

        sort_fields = {
            '': ('-created_at', '-pk'),
            'oldest': ('created_at', 'pk'),
            'name': ('name', 'pk'),
            'value_desc': ('-estimated_value', '-created_at', '-pk'),
        }
        return queryset.order_by(
            *sort_fields.get(filters.get('sort'), ('-created_at', '-pk'))
        )

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get(self.page_kwarg))
        return paginator, page, page.object_list, paginator.num_pages > 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.filter_form.cleaned_data
        summary = get_lead_dashboard_metrics(self.request.user)
        context.update({
            'filter_form': self.filter_form,
            'has_any_leads': Lead.objects.filter(owner=self.request.user).exists(),
            'has_filters': any((
                bool(filters.get('q', '').strip()),
                bool(filters.get('status')),
                bool(filters.get('source')),
            )),
            'total_leads': Lead.objects.filter(owner=self.request.user).count(),
            **summary,
        })
        return context


class LeadCreateView(LoginRequiredMixin, CreateView):
    login_url = 'login'
    model = Lead
    form_class = LeadForm
    template_name = 'crm/lead_form.html'
    success_url = reverse_lazy('lead_list')
    extra_context = {'title': 'Add Lead'}

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, 'Lead created successfully.')
        return response


class LeadDetailView(LoginRequiredMixin, DetailView):
    login_url = 'login'
    model = Lead
    template_name = 'crm/lead_detail.html'
    context_object_name = 'lead'

    def get_queryset(self):
        return Lead.objects.filter(owner=self.request.user).select_related(
            'converted_contact',
        )


class LeadUpdateView(LoginRequiredMixin, UpdateView):
    login_url = 'login'
    model = Lead
    form_class = LeadForm
    template_name = 'crm/lead_form.html'
    success_url = reverse_lazy('lead_list')
    extra_context = {'title': 'Edit Lead'}

    def get_queryset(self):
        return Lead.objects.filter(owner=self.request.user)

    def dispatch(self, request, *_args, **_kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *_args, **_kwargs)
        lead = self.get_object()
        if lead.status == 'converted':
            messages.warning(request, 'Converted leads cannot be edited.')
            return redirect('lead_detail', pk=lead.pk)
        return super().dispatch(request, *_args, **_kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Lead updated successfully.')
        return response


class LeadDeleteView(LoginRequiredMixin, DeleteView):
    login_url = 'login'
    model = Lead
    template_name = 'crm/lead_confirm_delete.html'
    context_object_name = 'lead'
    success_url = reverse_lazy('lead_list')

    def get_queryset(self):
        return Lead.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Lead deleted successfully.')
        return response


@login_required(login_url='login')
@require_POST
def lead_status_change(request, pk):
    lead = get_object_or_404(Lead, pk=pk, owner=request.user)
    status = request.POST.get('status')
    valid_statuses = {
        value for value, _ in Lead.STATUS_CHOICES
        if value != 'converted'
    }
    if status not in valid_statuses:
        return HttpResponseBadRequest('Select a valid lead status.')
    lead.status = status
    lead.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'Lead status updated.')
    return redirect('lead_detail', pk=lead.pk)


class LeadConvertView(LoginRequiredMixin, View):
    login_url = 'login'

    def dispatch(self, request, *_args, **_kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *_args, **_kwargs)
        self.lead = get_object_or_404(Lead, pk=_kwargs['pk'], owner=request.user)
        return super().dispatch(request, *_args, **_kwargs)

    def get(self, request, *_args, **_kwargs):
        if self.lead.status == 'converted':
            messages.warning(request, 'This lead has already been converted.')
            return redirect('lead_detail', pk=self.lead.pk)
        form = LeadConvertForm(initial={
            'create_deal': True,
            'create_company': bool(self.lead.company_name),
            'deal_title': f'{self.lead.name} deal',
            'deal_value': self.lead.estimated_value,
            'deal_stage': 'new',
        })
        return self.render(form)

    def post(self, request, *_args, **_kwargs):
        if self.lead.status == 'converted':
            messages.warning(request, 'This lead has already been converted.')
            return redirect('lead_detail', pk=self.lead.pk)
        form = LeadConvertForm(request.POST)
        if not form.is_valid():
            return self.render(form)
        cleaned = form.cleaned_data
        try:
            result = convert_lead(
                self.lead,
                request.user,
                create_deal=cleaned['create_deal'],
                deal_title=cleaned['deal_title'],
                deal_value=cleaned['deal_value'],
                deal_stage=cleaned['deal_stage'],
                company_option=cleaned['create_company'],
            )
        except ValidationError as error:
            form.add_error(None, error)
            return self.render(form, status=400)

        if result.linked_existing_contact:
            messages.warning(
                request,
                'An existing contact with the same email was linked instead '
                'of creating a duplicate.',
            )
        else:
            messages.success(request, 'Lead converted successfully.')
        return redirect('contact_detail', pk=result.contact.pk)

    def render(self, form, status=200):
        company_name = self.lead.company_name.strip()
        return render(self.request, 'crm/lead_convert.html', {
            'lead': self.lead,
            'form': form,
            'preview_company_name': company_name,
        }, status=status)


class CompanyListView(LoginRequiredMixin, ListView):
    login_url = 'login'
    model = Company
    template_name = 'crm/company_list.html'
    context_object_name = 'companies'
    paginate_by = 10

    def get_queryset(self):
        self.filter_form = CompanyFilterForm(self.request.GET)
        self.filter_form.is_valid()
        filters = self.filter_form.cleaned_data
        queryset = Company.objects.filter(owner=self.request.user).annotate(
            contact_count=Count('contacts', distinct=True),
            deal_count=Count('contacts__deals', distinct=True),
        )
        for currency, _ in Deal.CURRENCY_CHOICES:
            queryset = queryset.annotate(**{
                f'total_{currency.lower()}': Sum(
                    'contacts__deals__value',
                    filter=Q(contacts__deals__currency=currency),
                ),
            })

        query = filters.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(website__icontains=query)
                | Q(city__icontains=query)
                | Q(industry__icontains=query)
            )
        if filters.get('industry'):
            queryset = queryset.filter(industry=filters['industry'])
        sort_fields = {
            'name': ('name', 'pk'),
            'newest': ('-created_at', '-pk'),
            'contacts': ('-contact_count', 'name', 'pk'),
        }
        return queryset.order_by(*sort_fields.get(
            filters.get('sort'),
            ('name', 'pk'),
        ))

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get(self.page_kwarg))
        return paginator, page, page.object_list, paginator.num_pages > 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.filter_form.cleaned_data
        for company in context['companies']:
            company.currency_totals = [
                {
                    'currency': currency,
                    'value': getattr(company, f'total_{currency.lower()}'),
                }
                for currency, _ in Deal.CURRENCY_CHOICES
                if getattr(company, f'total_{currency.lower()}') is not None
            ]
        context['filter_form'] = self.filter_form
        context['has_companies'] = Company.objects.filter(
            owner=self.request.user,
        ).exists()
        context['has_filters'] = any((
            bool(filters.get('q', '').strip()),
            bool(filters.get('industry')),
        ))
        return context


class CompanyCreateView(LoginRequiredMixin, CreateView):
    login_url = 'login'
    model = Company
    form_class = CompanyForm
    template_name = 'crm/company_form.html'
    success_url = reverse_lazy('company_list')
    extra_context = {'title': 'Add Company'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, 'Company created successfully.')
        return response


class CompanyDetailView(LoginRequiredMixin, DetailView):
    login_url = 'login'
    model = Company
    template_name = 'crm/company_detail.html'
    context_object_name = 'company'

    def get_queryset(self):
        return Company.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.object
        contacts = Contact.objects.filter(
            owner=self.request.user,
            company=company,
        ).select_related('company')
        deals = Deal.objects.filter(
            contact__owner=self.request.user,
            contact__company=company,
        ).select_related('contact', 'contact__company')
        tasks = Task.objects.filter(
            owner=self.request.user,
            contact__owner=self.request.user,
            contact__company=company,
            status='pending',
        ).select_related('contact', 'deal').order_by('due_date', 'pk')
        summary = get_company_deal_summary(company, self.request.user)
        context.update({
            'contacts': contacts,
            'deals': deals,
            'company_tasks': tasks,
            'currency_summary': summary,
            'total_deals': sum(row['deal_count'] for row in summary),
            'total_open_pipeline': [
                {'currency': row['currency'], 'value': row['open_pipeline']}
                for row in summary if row['open_pipeline'] is not None
            ],
            'total_won_revenue': [
                {'currency': row['currency'], 'value': row['won_revenue']}
                for row in summary if row['won_revenue'] is not None
            ],
        })
        return context


class CompanyUpdateView(LoginRequiredMixin, UpdateView):
    login_url = 'login'
    model = Company
    form_class = CompanyForm
    template_name = 'crm/company_form.html'
    context_object_name = 'company'
    extra_context = {'title': 'Edit Company'}
    success_url = reverse_lazy('company_list')

    def get_queryset(self):
        return Company.objects.filter(owner=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Company updated successfully.')
        return response


class CompanyDeleteView(LoginRequiredMixin, DeleteView):
    login_url = 'login'
    model = Company
    template_name = 'crm/company_confirm_delete.html'
    context_object_name = 'company'
    success_url = reverse_lazy('company_list')

    def get_queryset(self):
        return Company.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['contact_count'] = self.object.contacts.filter(
            owner=self.request.user,
        ).count()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            'Company deleted successfully. Its contacts were kept.',
        )
        return response


@login_required(login_url='login')
@require_POST
def company_quick_create(request):
    form = CompanyQuickCreateForm(request.POST, user=request.user)
    if not form.is_valid():
        return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
    company = Company.objects.create(
        owner=request.user,
        name=form.cleaned_data['name'],
    )
    return JsonResponse({
        'id': company.pk,
        'name': company.name,
        'detail_url': reverse('company_detail', args=[company.pk]),
    }, status=201)


class DealListView(LoginRequiredMixin, ListView):
    login_url = 'login'
    model = Deal
    template_name = 'crm/deal_list.html'
    context_object_name = 'deals'
    paginate_by = 10

    def get_queryset(self):
        self.filter_form = DealFilterForm(self.request.GET)
        self.filter_form.is_valid()
        filters = self.filter_form.cleaned_data
        queryset = Deal.objects.filter(
            contact__owner=self.request.user,
        ).select_related('contact', 'contact__company')

        query = filters.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(contact__name__icontains=query)
            )
        if filters.get('stage'):
            queryset = queryset.filter(stage=filters['stage'])
        if filters.get('currency'):
            queryset = queryset.filter(currency=filters['currency'])
        if filters.get('min_value') is not None:
            queryset = queryset.filter(value__gte=filters['min_value'])
        if filters.get('max_value') is not None:
            queryset = queryset.filter(value__lte=filters['max_value'])

        sort_fields = {
            '': ('-created_at', '-pk'),
            'oldest': ('created_at', 'pk'),
            'value_desc': ('-value', '-pk'),
            'value_asc': ('value', 'pk'),
            'title_asc': ('title', 'pk'),
        }
        return queryset.order_by(
            *sort_fields.get(filters.get('sort'), ('-created_at',))
        )

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get(self.page_kwarg))
        return paginator, page, page.object_list, paginator.num_pages > 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.filter_form.cleaned_data
        context['has_contacts'] = Contact.objects.filter(
            owner=self.request.user,
        ).exists()
        context['has_any_deals'] = Deal.objects.filter(
            contact__owner=self.request.user,
        ).exists()
        context['filter_form'] = self.filter_form
        board_params = {
            key: filters.get(key)
            for key in ('q', 'currency')
            if filters.get(key)
        }
        context['board_query'] = urlencode(board_params)
        context['has_filters'] = any((
            bool(filters.get('q', '').strip()),
            bool(filters.get('stage')),
            bool(filters.get('currency')),
            filters.get('min_value') is not None,
            filters.get('max_value') is not None,
        ))
        context['filtered_value_totals'] = (
            self.object_list.values('currency')
            .annotate(total=Sum('value'))
            .order_by('currency')
        )
        return context


@login_required(login_url='login')
def deal_board(request):
    filter_form = DealBoardFilterForm(request.GET)
    filter_form.is_valid()
    filters = filter_form.cleaned_data
    columns = build_deal_board(
        request.user,
        query=filters.get('q', ''),
        currency=filters.get('currency', ''),
    )
    return render(request, 'crm/deal_board.html', {
        'columns': columns,
        'filter_form': filter_form,
        'stage_choices': Deal.STAGE_CHOICES,
        'list_query': urlencode({
            key: filters.get(key)
            for key in ('q', 'currency')
            if filters.get(key)
        }),
        'has_deals': Deal.objects.filter(
            contact__owner=request.user,
        ).exists(),
        'has_contacts': Contact.objects.filter(owner=request.user).exists(),
        'has_contacts': Contact.objects.filter(owner=request.user).exists(),
    })


@login_required(login_url='login')
def move_deal_view(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    deal = get_object_or_404(
        Deal.objects.select_related('contact').filter(
            contact__owner=request.user,
        ),
        pk=pk,
    )
    new_stage = request.POST.get('stage', '')
    previous_stage = deal.stage
    try:
        move_deal(deal, new_stage, request.user)
    except ValidationError as error:
        return JsonResponse({'error': error.messages[0]}, status=400)

    affected_stages = {previous_stage, new_stage}
    totals = get_deal_stage_totals(
        request.user,
        stages=affected_stages,
        query=request.POST.get('q', ''),
        currency=request.POST.get('currency', ''),
    )
    columns = {}
    for stage in affected_stages:
        rows = [row for row in totals if row['stage'] == stage]
        columns[stage] = {
            'count': sum(row['count'] for row in rows),
            'totals': [
                {
                    'currency': row['currency'],
                    'value': str(row['total']),
                    'formatted': display_currency(row['total'], row['currency']),
                }
                for row in rows
            ],
        }
    return JsonResponse({
        'source_stage': previous_stage,
        'target_stage': new_stage,
        'stage_label': dict(Deal.STAGE_CHOICES)[new_stage],
        'stage_badge_class': stage_badge_class(new_stage),
        'stage_changed_at': deal.stage_changed_at.isoformat(),
        'columns': columns,
    })


class DealCreateView(LoginRequiredMixin, CreateView):
    login_url = 'login'
    model = Deal
    form_class = DealForm
    template_name = 'crm/deal_form.html'
    success_url = reverse_lazy('deal_list')
    extra_context = {'title': 'Add Deal'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        stage = self.request.GET.get('stage', '')
        if stage in {value for value, _ in Deal.STAGE_CHOICES}:
            initial['stage'] = stage
        contact_id = self.request.GET.get('contact', '')
        if contact_id.isdecimal():
            contact = Contact.objects.filter(
                pk=int(contact_id),
                owner=self.request.user,
            ).first()
            if contact is not None:
                initial['contact'] = contact.pk
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['has_contacts'] = Contact.objects.filter(
            owner=self.request.user,
        ).exists()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Deal created successfully.')
        return response


class DealDetailView(LoginRequiredMixin, DetailView):
    login_url = 'login'
    model = Deal
    template_name = 'crm/deal_detail.html'
    context_object_name = 'deal'

    def get_queryset(self):
        return Deal.objects.filter(
            contact__owner=self.request.user,
        ).select_related('contact', 'contact__company')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(deal_note_context(
            self.object,
            self.request.user,
            context.get('note_form'),
        ))
        context['deal_tasks'] = (
            Task.objects.filter(
                owner=self.request.user,
                deal=self.object,
                status='pending',
            )
            .select_related('contact')
            .order_by('due_date', 'pk')
        )
        context['deal_task_add_url'] = (
            f"{reverse('task_add')}?{urlencode({
                'deal': self.object.pk,
                'next': reverse('deal_detail', args=[self.object.pk]),
            })}"
        )
        context['deal_timeline'] = get_deal_timeline(
            self.object,
            self.request.user,
            limit=10,
        )
        return context


class DealUpdateView(LoginRequiredMixin, UpdateView):
    login_url = 'login'
    model = Deal
    form_class = DealForm
    template_name = 'crm/deal_form.html'
    success_url = reverse_lazy('deal_list')
    extra_context = {'title': 'Edit Deal'}

    def get_queryset(self):
        return Deal.objects.filter(contact__owner=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['has_contacts'] = True
        return context

    def form_valid(self, form):
        requested_stage = form.cleaned_data['stage']
        previous_stage = Deal.objects.values_list('stage', flat=True).get(
            pk=self.object.pk,
        )
        form.instance.stage = previous_stage
        with transaction.atomic():
            response = super().form_valid(form)
            if requested_stage != previous_stage:
                move_deal(self.object, requested_stage, self.request.user)
        messages.success(self.request, 'Deal updated successfully.')
        return response


class DealDeleteView(LoginRequiredMixin, DeleteView):
    login_url = 'login'
    model = Deal
    template_name = 'crm/deal_confirm_delete.html'
    context_object_name = 'deal'
    success_url = reverse_lazy('deal_list')

    def get_queryset(self):
        return Deal.objects.filter(contact__owner=self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Deal deleted successfully.')
        return response


class TaskListView(LoginRequiredMixin, ListView):
    login_url = 'login'
    model = Task
    template_name = 'crm/task_list.html'
    context_object_name = 'tasks'
    paginate_by = 10
    allowed_tabs = {'pending', 'overdue', 'today', 'completed', 'all'}

    def get_queryset(self):
        tab = self.request.GET.get('tab', 'pending')
        self.active_tab = tab if tab in self.allowed_tabs else 'pending'
        queryset = Task.objects.filter(owner=self.request.user).select_related(
            'contact',
            'deal',
        )
        if self.active_tab == 'pending':
            queryset = queryset.filter(status='pending')
        elif self.active_tab == 'overdue':
            queryset = queryset.filter(
                status='pending',
                due_date__lt=timezone.now(),
            )
        elif self.active_tab == 'today':
            day_start, day_end = local_day_bounds()
            queryset = queryset.filter(
                status='pending',
                due_date__gte=day_start,
                due_date__lt=day_end,
            )
        elif self.active_tab == 'completed':
            queryset = queryset.filter(status='completed')

        if self.active_tab == 'completed':
            return queryset.order_by('-completed_at', '-pk')
        return queryset.order_by('due_date', 'pk')

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get(self.page_kwarg))
        return paginator, page, page.object_list, paginator.num_pages > 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.active_tab
        context['has_any_tasks'] = Task.objects.filter(
            owner=self.request.user,
        ).exists()
        context['tabs'] = (
            ('pending', 'Pending'),
            ('overdue', 'Overdue'),
            ('today', 'Today'),
            ('completed', 'Completed'),
            ('all', 'All'),
        )
        return context


class TaskCreateView(LoginRequiredMixin, CreateView):
    login_url = 'login'
    model = Task
    form_class = TaskForm
    template_name = 'crm/task_form.html'
    extra_context = {'title': 'Add Task'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        contact_id = self.request.GET.get('contact', '')
        deal_id = self.request.GET.get('deal', '')
        if deal_id.isdecimal():
            deal = Deal.objects.filter(
                pk=int(deal_id),
                contact__owner=self.request.user,
            ).select_related('contact').first()
            if deal:
                initial.update({'deal': deal.pk, 'contact': deal.contact_id})
        elif contact_id.isdecimal():
            contact = Contact.objects.filter(
                pk=int(contact_id),
                owner=self.request.user,
            ).first()
            if contact:
                initial['contact'] = contact.pk
        return initial

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, 'Task created successfully.')
        return response

    def get_success_url(self):
        return task_return_url(self.request)


class TaskUpdateView(LoginRequiredMixin, UpdateView):
    login_url = 'login'
    model = Task
    form_class = TaskForm
    template_name = 'crm/task_form.html'
    context_object_name = 'task'
    extra_context = {'title': 'Edit Task'}

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        return task_return_url(self.request)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Task updated successfully.')
        return response


class TaskDeleteView(LoginRequiredMixin, DeleteView):
    login_url = 'login'
    model = Task
    template_name = 'crm/task_confirm_delete.html'
    context_object_name = 'task'
    success_url = reverse_lazy('task_list')

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['return_url'] = task_return_url(self.request)
        return context

    def form_valid(self, form):
        self.success_url = task_return_url(self.request)
        response = super().form_valid(form)
        messages.success(self.request, 'Task deleted successfully.')
        return response


def task_return_url(request):
    requested_url = request.POST.get('next') or request.GET.get('next')
    if requested_url and url_has_allowed_host_and_scheme(
        requested_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return requested_url
    return reverse('task_list')


@login_required(login_url='login')
def toggle_task_complete(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    task = get_object_or_404(Task, pk=pk, owner=request.user)
    if task.status == 'pending':
        task.status = 'completed'
        task.completed_at = timezone.now()
        messages.success(request, 'Task marked complete.')
    else:
        task.status = 'pending'
        task.completed_at = None
        messages.success(request, 'Task reopened.')
    task.save(update_fields=['status', 'completed_at', 'updated_at'])
    return redirect(task_return_url(request))