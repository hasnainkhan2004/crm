from django import template
from django.utils import timezone

register = template.Library()

@register.filter
def phone_digits(value):
    return ''.join(character for character in str(value or '') if character.isdigit())


CURRENCY_SYMBOLS = {
    'PKR': 'Rs',
    'USD': '$',
    'AED': 'AED',
    'GBP': '£',
    'EUR': '€',
}


@register.filter
def display_currency(value, currency='PKR'):
    amount = f'{value:,.2f}'.rstrip('0').rstrip('.')
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    if symbol in ('$', '£', '€'):
        return f'{symbol}{amount}'
    return f'{symbol} {amount}'


@register.filter
def lead_status_badge_class(status):
    return {
        'new': 'secondary',
        'contacted': 'info text-dark',
        'qualified': 'primary',
        'unqualified': 'dark',
        'converted': 'success',
    }.get(status, 'secondary')


@register.filter
def days_in_stage(value):
    if value is None:
        return 0
    return max(0, (timezone.now() - value).days)


@register.filter
def stage_badge_class(stage):
    return {
        'new': 'secondary',
        'contacted': 'info text-dark',
        'negotiation': 'warning text-dark',
        'won': 'success',
        'lost': 'danger',
    }.get(stage, 'secondary')
