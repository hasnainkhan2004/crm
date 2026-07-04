import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from .models import Contact, Deal, Activity
from .forms import ContactForm, DealForm, ActivityForm


@login_required(login_url='login')
def dashboard(request):
    total_contacts = Contact.objects.count()
    total_deals = Deal.objects.count()
    total_value = Deal.objects.aggregate(Sum('value'))['value__sum'] or 0
    stage_counts = Deal.objects.values('stage').annotate(count=Count('id'))

    chart_labels = [Deal.STAGE_CHOICES[i][1] for i in range(len(Deal.STAGE_CHOICES))]
    stage_dict = {item['stage']: item['count'] for item in stage_counts}
    chart_data = [stage_dict.get(choice[0], 0) for choice in Deal.STAGE_CHOICES]

    context = {
        'total_contacts': total_contacts,
        'total_deals': total_deals,
        'total_value': total_value,
        'stage_counts': stage_counts,
        'chart_labels': json.dumps(chart_labels),
        'chart_data': json.dumps(chart_data),
    }
    return render(request, 'crm/dashboard.html', context)


@login_required(login_url='login')
def contact_list(request):
    query = request.GET.get('q', '')
    contacts = Contact.objects.all().order_by('-created_at')
    if query:
        contacts = contacts.filter(name__icontains=query) | contacts.filter(company__icontains=query)
    return render(request, 'crm/contact_list.html', {'contacts': contacts, 'query': query})


@login_required(login_url='login')
def contact_add(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('contact_list')
    else:
        form = ContactForm()
    return render(request, 'crm/contact_form.html', {'form': form, 'title': 'Add Contact'})


@login_required(login_url='login')
def contact_edit(request, pk):
    contact = get_object_or_404(Contact, pk=pk)
    if request.method == 'POST':
        form = ContactForm(request.POST, instance=contact)
        if form.is_valid():
            form.save()
            return redirect('contact_list')
    else:
        form = ContactForm(instance=contact)
    return render(request, 'crm/contact_form.html', {'form': form, 'title': 'Edit Contact'})


@login_required(login_url='login')
def contact_delete(request, pk):
    contact = get_object_or_404(Contact, pk=pk)
    if request.method == 'POST':
        contact.delete()
        return redirect('contact_list')
    return render(request, 'crm/contact_confirm_delete.html', {'contact': contact})


@login_required(login_url='login')
def contact_detail(request, pk):
    contact = get_object_or_404(Contact, pk=pk)
    deals = contact.deals.all()
    activities = contact.activities.all().order_by('-created_at')

    if request.method == 'POST':
        form = ActivityForm(request.POST)
        if form.is_valid():
            activity = form.save(commit=False)
            activity.contact = contact
            activity.save()
            return redirect('contact_detail', pk=contact.pk)
    else:
        form = ActivityForm()

    context = {
        'contact': contact,
        'deals': deals,
        'activities': activities,
        'form': form,
    }
    return render(request, 'crm/contact_detail.html', context)


@login_required(login_url='login')
def deal_list(request):
    query = request.GET.get('q', '')
    stage_filter = request.GET.get('stage', '')

    deals = Deal.objects.all().order_by('-created_at')
    if query:
        deals = deals.filter(title__icontains=query) | deals.filter(contact__name__icontains=query)
    if stage_filter:
        deals = deals.filter(stage=stage_filter)

    context = {
        'deals': deals,
        'query': query,
        'stage_filter': stage_filter,
        'stage_choices': Deal.STAGE_CHOICES,
    }
    return render(request, 'crm/deal_list.html', context)


@login_required(login_url='login')
def deal_add(request):
    if request.method == 'POST':
        form = DealForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('deal_list')
    else:
        form = DealForm()
    return render(request, 'crm/deal_form.html', {'form': form, 'title': 'Add Deal'})


@login_required(login_url='login')
def deal_edit(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if request.method == 'POST':
        form = DealForm(request.POST, instance=deal)
        if form.is_valid():
            form.save()
            return redirect('deal_list')
    else:
        form = DealForm(instance=deal)
    return render(request, 'crm/deal_form.html', {'form': form, 'title': 'Edit Deal'})


@login_required(login_url='login')
def deal_delete(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if request.method == 'POST':
        deal.delete()
        return redirect('deal_list')
    return render(request, 'crm/deal_confirm_delete.html', {'deal': deal})