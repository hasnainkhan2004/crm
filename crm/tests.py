import json
from decimal import Decimal
from datetime import timedelta
from html import unescape
from urllib.parse import parse_qs, urlsplit
import re
from unittest.mock import patch

from django.test import Client, TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from .forms import DealForm
from .models import Company, Contact, Deal, DealStageHistory, Lead, Note, Task
from .services import (
    build_deal_board,
    calculate_sales_kpis,
    convert_lead,
    get_company_deal_summary,
    get_contact_summary,
    get_contact_timeline,
    get_recent_contact_activity,
)
from .templatetags.crm_extras import display_currency


User = get_user_model()


class SalesKpiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='sales-owner',
            email='sales-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='other-owner',
            email='other-owner@example.com',
            password='AnotherPassword!248',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Sales contact',
            email='sales-contact@example.com',
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Other contact',
            email='other-sales-contact@example.com',
        )

    def create_deal(self, title, stage, value, currency='PKR', contact=None):
        return Deal.objects.create(
            contact=contact or self.contact,
            title=title,
            stage=stage,
            value=value,
            currency=currency,
        )

    def test_pipeline_and_closed_values_are_grouped_by_currency(self):
        self.create_deal('New PKR', 'new', 1000)
        self.create_deal('Contacted PKR', 'contacted', 500)
        self.create_deal('Negotiation USD', 'negotiation', 1200, 'USD')
        self.create_deal('Won PKR', 'won', 2000)
        self.create_deal('Lost USD', 'lost', 300, 'USD')

        kpis = calculate_sales_kpis(self.owner)

        self.assertEqual(kpis['open_pipeline_values'], [
            {'currency': 'PKR', 'value': 1500},
            {'currency': 'USD', 'value': 1200},
        ])
        self.assertEqual(kpis['won_revenue_values'], [
            {'currency': 'PKR', 'value': 2000},
        ])
        self.assertEqual(kpis['lost_values'], [
            {'currency': 'USD', 'value': 300},
        ])

    def test_win_rate_uses_only_won_and_lost_deals(self):
        self.create_deal('Open deal', 'new', 100)
        self.create_deal('Won deal', 'won', 100)
        self.create_deal('Lost deal', 'lost', 100)
        self.create_deal('Another won deal', 'won', 100)

        self.assertEqual(
            calculate_sales_kpis(self.owner)['win_rate'],
            Decimal('66.67'),
        )

    def test_win_rate_is_zero_when_there_are_no_closed_deals(self):
        self.create_deal('Open deal', 'negotiation', 250)

        self.assertEqual(
            calculate_sales_kpis(self.owner)['win_rate'],
            Decimal('0'),
        )

    def test_kpis_exclude_other_users_deals(self):
        self.create_deal('Own won deal', 'won', 100)
        self.create_deal(
            'Other lost deal',
            'lost',
            9000,
            contact=self.other_contact,
        )

        kpis = calculate_sales_kpis(self.owner)

        self.assertEqual(kpis['won_revenue_values'], [
            {'currency': 'PKR', 'value': 100},
        ])
        self.assertEqual(kpis['lost_values'], [])
        self.assertEqual(kpis['win_rate'], 100)


class ContactCrudTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='contact-owner',
            email='contact-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='contact-other',
            email='contact-other@example.com',
            password='AnotherPassword!248',
        )
        self.company = Company.objects.create(
            owner=self.owner,
            name='Existing Co',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Existing Contact',
            email='existing@example.com',
            phone='+1 (555) 010-1010',
            company=self.company,
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Private Contact',
            email='private@example.com',
        )

    def test_contact_pages_require_login(self):
        urls = [
            reverse('contact_list'),
            reverse('contact_add'),
            reverse('contact_detail', args=[self.contact.pk]),
            reverse('contact_edit', args=[self.contact.pk]),
            reverse('contact_delete', args=[self.contact.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f'{reverse("login")}?next={url}')

    def test_list_and_detail_only_show_the_logged_in_users_contacts(self):
        self.client.force_login(self.owner)

        list_response = self.client.get(reverse('contact_list'))
        detail_response = self.client.get(
            reverse('contact_detail', args=[self.contact.pk])
        )

        self.assertContains(list_response, 'Existing Contact')
        self.assertNotContains(list_response, 'Private Contact')
        self.assertContains(detail_response, 'Existing Contact')
        self.assertNotContains(detail_response, 'Private Contact')
        self.assertContains(detail_response, reverse('deal_add'))

    def test_other_users_contact_is_404_for_detail_edit_and_delete(self):
        self.client.force_login(self.owner)
        other_detail = reverse('contact_detail', args=[self.other_contact.pk])
        other_edit = reverse('contact_edit', args=[self.other_contact.pk])
        other_delete = reverse('contact_delete', args=[self.other_contact.pk])

        for url in (other_detail, other_edit, other_delete):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

        self.assertEqual(
            self.client.post(other_edit, {
                'name': 'Changed',
                'email': 'changed@example.com',
                'phone': '',
                'company': '',
            }).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(other_delete, {}).status_code,
            404,
        )
        self.other_contact.refresh_from_db()
        self.assertEqual(self.other_contact.name, 'Private Contact')

    def test_create_contact_sets_owner_and_shows_success_message(self):
        self.client.force_login(self.owner)

        response = self.client.post(reverse('contact_add'), {
            'name': 'New Contact',
            'email': 'new@example.com',
            'phone': '+44 (20) 1234-5678',
            'company': '',
        })

        self.assertRedirects(response, reverse('contact_list'))
        new_contact = Contact.objects.get(email='new@example.com')
        self.assertEqual(new_contact.owner, self.owner)
        self.assertEqual(new_contact.phone, '+44 (20) 1234-5678')
        self.assertIn('Contact created successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])

    def test_create_rejects_duplicate_email_and_invalid_phone(self):
        self.client.force_login(self.owner)

        duplicate_response = self.client.post(reverse('contact_add'), {
            'name': 'Duplicate',
            'email': 'EXISTING@example.com',
            'phone': '',
            'company': '',
        })
        self.assertContains(
            duplicate_response,
            'You already have a contact with this email address.',
        )
        self.assertFalse(Contact.objects.filter(name='Duplicate').exists())

        phone_response = self.client.post(reverse('contact_add'), {
            'name': 'Invalid Phone',
            'email': 'phone@example.com',
            'phone': '123abc',
            'company': '',
        })
        self.assertContains(
            phone_response,
            'Enter a phone number using only digits, +, -, spaces, and brackets.',
        )
        self.assertFalse(Contact.objects.filter(name='Invalid Phone').exists())

    def test_update_contact_and_show_success_message(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('contact_edit', args=[self.contact.pk]),
            {
                'name': 'Updated Contact',
                'email': 'updated@example.com',
                'phone': '555-1234',
                'company': self.company.pk,
            },
        )

        self.assertRedirects(response, reverse('contact_list'))
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.name, 'Updated Contact')
        self.assertEqual(self.contact.owner, self.owner)
        self.assertIn('Contact updated successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])

    def test_delete_warns_about_linked_deals_and_cascades_on_post(self):
        Deal.objects.create(
            contact=self.contact,
            title='Linked Deal',
            value=100,
        )
        self.client.force_login(self.owner)

        confirmation = self.client.get(
            reverse('contact_delete', args=[self.contact.pk])
        )
        self.assertContains(confirmation, 'permanently delete 1 linked deal')

        response = self.client.post(
            reverse('contact_delete', args=[self.contact.pk]),
        )

        self.assertRedirects(response, reverse('contact_list'))
        self.assertFalse(Contact.objects.filter(pk=self.contact.pk).exists())
        self.assertFalse(Deal.objects.filter(title='Linked Deal').exists())
        self.assertIn('Contact deleted successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])


class CompanyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='company-owner',
            email='company-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='company-other',
            email='company-other@example.com',
            password='AnotherPassword!248',
        )
        self.company = Company.objects.create(
            owner=self.owner,
            name='Acme Incorporated',
            industry='technology',
            city='Karachi',
        )
        self.other_company = Company.objects.create(
            owner=self.other_user,
            name='Private Acme',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Acme Contact',
            email='acme-contact@example.com',
            company=self.company,
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Private Contact',
            email='private-company-contact@example.com',
            company=self.other_company,
        )
        self.deal_pkr = Deal.objects.create(
            contact=self.contact,
            title='Acme open deal',
            stage='negotiation',
            value=Decimal('1200'),
            currency='PKR',
        )
        self.deal_usd = Deal.objects.create(
            contact=self.contact,
            title='Acme won deal',
            stage='won',
            value=Decimal('500'),
            currency='USD',
        )
        self.client.force_login(self.owner)

    def company_post_data(self, **overrides):
        data = {
            'name': 'New Company',
            'website': 'https://example.com',
            'industry': 'services',
            'phone': '',
            'email': '',
            'address': '',
            'city': '',
            'country': '',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_company_pages_and_quick_create_require_login(self):
        self.client.logout()
        urls = [
            reverse('company_list'),
            reverse('company_add'),
            reverse('company_detail', args=[self.company.pk]),
            reverse('company_edit', args=[self.company.pk]),
            reverse('company_delete', args=[self.company.pk]),
            reverse('company_quick_create'),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)

    def test_company_owner_isolation_for_detail_edit_delete_and_quick_create(self):
        for name in ('company_detail', 'company_edit', 'company_delete'):
            url = reverse(name, args=[self.other_company.pk])
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse('company_edit', args=[self.other_company.pk]),
                self.company_post_data(name='Changed'),
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('company_delete', args=[self.other_company.pk]),
            ).status_code,
            404,
        )
        listing = self.client.get(reverse('company_list'))
        self.assertContains(listing, 'Acme Incorporated')
        self.assertNotContains(listing, 'Private Acme')

        quick = self.client.post(
            reverse('company_quick_create'),
            {'name': 'Not for other owner'},
        )
        self.assertEqual(quick.status_code, 201)
        self.assertEqual(
            Company.objects.get(name='Not for other owner').owner,
            self.owner,
        )

    def test_company_create_edit_and_case_insensitive_duplicate_rejection(self):
        create = self.client.post(
            reverse('company_add'),
            self.company_post_data(name='New Company'),
        )
        self.assertRedirects(create, reverse('company_list'))
        created = Company.objects.get(name='New Company')
        self.assertEqual(created.owner, self.owner)

        duplicate = self.client.post(
            reverse('company_add'),
            self.company_post_data(name='  acme incorporated '),
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(
            duplicate,
            'You already have a company with this name.',
        )

        update = self.client.post(
            reverse('company_edit', args=[created.pk]),
            self.company_post_data(name='New Company Renamed'),
        )
        self.assertRedirects(update, reverse('company_list'))
        created.refresh_from_db()
        self.assertEqual(created.name, 'New Company Renamed')

    def test_company_delete_unlinks_but_keeps_contacts(self):
        confirmation = self.client.get(
            reverse('company_delete', args=[self.company.pk]),
        )
        self.assertContains(confirmation, '1 contact will be unlinked')
        response = self.client.post(
            reverse('company_delete', args=[self.company.pk]),
        )
        self.assertRedirects(response, reverse('company_list'))
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.company)
        self.assertTrue(Contact.objects.filter(pk=self.contact.pk).exists())

    def test_contact_dropdown_preselection_and_tampered_company_rejection(self):
        response = self.client.get(
            reverse('contact_add'),
            {'company': self.company.pk},
        )
        self.assertEqual(
            response.context['form']['company'].value(),
            self.company.pk,
        )
        self.assertContains(response, 'Acme Incorporated')
        self.assertNotContains(response, 'Private Acme')
        self.assertContains(
            self.client.get(
                reverse('contact_detail', args=[self.contact.pk]),
            ),
            reverse('company_detail', args=[self.company.pk]),
        )

        tampered = self.client.post(
            reverse('contact_add'),
            {
                'name': 'Tampered company contact',
                'email': 'tampered-company@example.com',
                'phone': '',
                'company': self.other_company.pk,
            },
        )
        self.assertEqual(tampered.status_code, 200)
        self.assertFalse(Contact.objects.filter(
            email='tampered-company@example.com',
        ).exists())

    def test_quick_company_create_requires_csrf_and_creates_json_response(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        url = reverse('company_quick_create')
        self.assertEqual(client.post(url, {'name': 'Quick Co'}).status_code, 403)
        contact_form = client.get(reverse('contact_add'))
        token = re.search(
            rb'name="csrfmiddlewaretoken" value="([^"]+)"',
            contact_form.content,
        ).group(1).decode()
        response = client.post(
            url,
            {'name': 'Quick Co'},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['name'], 'Quick Co')
        self.assertEqual(
            Company.objects.get(name='Quick Co').owner,
            self.owner,
        )
        self.assertEqual(client.get(url).status_code, 405)

    def test_company_detail_summaries_group_currency_and_owner_records(self):
        response = self.client.get(
            reverse('company_detail', args=[self.company.pk]),
        )
        self.assertContains(response, 'Acme open deal')
        self.assertContains(response, 'Acme won deal')
        self.assertNotContains(response, 'Private')
        self.assertEqual(response.context['total_deals'], 2)
        self.assertEqual(response.context['total_open_pipeline'], [
            {'currency': 'PKR', 'value': Decimal('1200.00')},
        ])
        self.assertEqual(response.context['total_won_revenue'], [
            {'currency': 'USD', 'value': Decimal('500.00')},
        ])
        self.assertContains(
            self.client.get(reverse('deal_detail', args=[self.deal_pkr.pk])),
            reverse('company_detail', args=[self.company.pk]),
        )
        self.assertContains(
            self.client.get(reverse('deal_board')),
            reverse('company_detail', args=[self.company.pk]),
        )
        self.assertEqual(
            get_company_deal_summary(self.company, self.owner),
            [
                {
                    'currency': 'PKR',
                    'deal_count': 1,
                    'open_pipeline': Decimal('1200.00'),
                    'won_revenue': None,
                },
                {
                    'currency': 'USD',
                    'deal_count': 1,
                    'open_pipeline': None,
                    'won_revenue': Decimal('500.00'),
                },
            ],
        )

    def test_contact_search_and_company_filter_match_company_relation(self):
        search = self.client.get(
            reverse('contact_list'),
            {'q': 'Acme Incorporated'},
        )
        self.assertEqual(search.context['paginator'].count, 1)
        self.assertContains(search, 'Acme Contact')

        filtered = self.client.get(
            reverse('contact_list'),
            {'company': self.company.pk},
        )
        self.assertEqual(filtered.context['paginator'].count, 1)
        self.assertEqual(
            filtered.context['filter_form']['company'].value(),
            str(self.company.pk),
        )
        self.assertContains(filtered, reverse('company_detail', args=[self.company.pk]))

    def test_company_list_aggregates_currency_totals_and_keeps_filters_on_pages(self):
        response = self.client.get(
            reverse('company_list'),
            {'q': 'Acme', 'industry': 'technology', 'sort': 'name'},
        )
        self.assertEqual(response.status_code, 200)
        company = response.context['page_obj'].object_list[0]
        self.assertEqual(company.contact_count, 1)
        self.assertEqual(company.deal_count, 2)
        self.assertEqual(company.currency_totals, [
            {'currency': 'PKR', 'value': Decimal('1200.00')},
            {'currency': 'USD', 'value': Decimal('500.00')},
        ])
        self.assertContains(response, 'Rs 1,200')
        self.assertContains(response, '$500')

    def test_company_list_paginates_and_preserves_filters(self):
        for index in range(11):
            Company.objects.create(
                owner=self.owner,
                name=f'Acme Branch {index:02}',
                industry='technology',
            )
        response = self.client.get(
            reverse('company_list'),
            {'q': 'Acme', 'industry': 'technology', 'sort': 'name'},
        )
        self.assertEqual(response.context['paginator'].count, 12)
        self.assertEqual(len(response.context['page_obj'].object_list), 10)
        page_links = re.findall(
            r'href="([^"]+)"[^>]*>2</a>',
            response.content.decode(),
        )
        self.assertTrue(page_links)
        params = parse_qs(urlsplit(unescape(page_links[0])).query)
        self.assertEqual(params['q'], ['Acme'])
        self.assertEqual(params['industry'], ['technology'])
        self.assertEqual(params['sort'], ['name'])
        self.assertEqual(params['page'], ['2'])


class CompanyDataMigrationTests(TransactionTestCase):
    migrate_from = ('crm', '0010_alter_dealstagehistory_changed_at')
    migrate_to = ('crm', '0012_remove_contact_company_name_old')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        UserModel = get_user_model()
        user = UserModel.objects.create_user(
            username='legacy-company-owner',
            password='irrelevant',
        )
        LegacyContact = old_apps.get_model('crm', 'Contact')
        self.legacy_contact_ids = []
        for index, legacy_company_name in enumerate(
            ('Acme', 'acme ', ' ACME'),
        ):
            contact = LegacyContact.objects.create(
                owner_id=user.pk,
                name=f'Legacy contact {index}',
                email=f'legacy-contact-{index}@example.com',
                phone='',
                company=legacy_company_name,
            )
            self.legacy_contact_ids.append(contact.pk)

    def test_existing_company_text_is_linked_and_duplicates_are_merged(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        migrated_apps = executor.loader.project_state([self.migrate_to]).apps
        CompanyModel = migrated_apps.get_model('crm', 'Company')
        ContactModel = migrated_apps.get_model('crm', 'Contact')

        contacts = ContactModel.objects.filter(
            pk__in=self.legacy_contact_ids,
        )
        linked_company_ids = set(
            contacts.values_list('company_id', flat=True)
        )
        self.assertEqual(len(linked_company_ids), 1)
        self.assertNotIn(None, linked_company_ids)
        self.assertEqual(
            CompanyModel.objects.filter(
                pk=next(iter(linked_company_ids)),
            ).get().name,
            'Acme',
        )

        intermediate = ('crm', '0011_contact_company_name_old_company_and_more')
        MigrationExecutor(connection).migrate([intermediate])
        intermediate_apps = MigrationExecutor(
            connection,
        ).loader.project_state([intermediate]).apps
        IntermediateContact = intermediate_apps.get_model('crm', 'Contact')
        self.assertEqual(
            set(IntermediateContact.objects.filter(
                pk__in=self.legacy_contact_ids,
            ).values_list('company_name_old', flat=True)),
            {'Acme'},
        )

        MigrationExecutor(connection).migrate([self.migrate_from])
        legacy_apps = MigrationExecutor(
            connection,
        ).loader.project_state([self.migrate_from]).apps
        LegacyContact = legacy_apps.get_model('crm', 'Contact')
        self.assertEqual(
            set(LegacyContact.objects.filter(
                pk__in=self.legacy_contact_ids,
            ).values_list('company', flat=True)),
            {'Acme'},
        )
        MigrationExecutor(connection).migrate([self.migrate_to])

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class DealCrudTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='deal-owner',
            email='deal-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='deal-other',
            email='deal-other@example.com',
            password='AnotherPassword!248',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Deal Contact',
            email='deal-contact@example.com',
        )
        self.second_contact = Contact.objects.create(
            owner=self.owner,
            name='Second Contact',
            email='second-contact@example.com',
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Other User Contact',
            email='other-contact@example.com',
        )
        self.deal = Deal.objects.create(
            contact=self.contact,
            title='Existing Deal',
            stage='contacted',
            value=1500,
            currency='USD',
        )

    def valid_data(self, **overrides):
        data = {
            'contact': self.contact.pk,
            'title': 'New Deal',
            'stage': 'new',
            'value': '1500.00',
            'currency': 'PKR',
        }
        data.update(overrides)
        return data

    def test_deal_pages_require_login(self):
        urls = [
            reverse('deal_list'),
            reverse('deal_add'),
            reverse('deal_detail', args=[self.deal.pk]),
            reverse('deal_edit', args=[self.deal.pk]),
            reverse('deal_delete', args=[self.deal.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f'{reverse("login")}?next={url}')

    def test_deal_list_and_detail_show_formatted_currency_and_stage_badge(self):
        self.client.force_login(self.owner)

        list_response = self.client.get(reverse('deal_list'))
        detail_response = self.client.get(
            reverse('deal_detail', args=[self.deal.pk])
        )

        self.assertContains(list_response, '$1,500')
        self.assertContains(list_response, 'Contacted')
        self.assertContains(list_response, 'bg-info')
        self.assertContains(detail_response, '$1,500')
        self.assertContains(detail_response, 'Deal Contact')
        self.assertContains(detail_response, reverse('contact_detail', args=[self.contact.pk]))

    def test_contact_dropdown_only_contains_contacts_owned_by_user(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse('deal_add'))
        edit_response = self.client.get(
            reverse('deal_edit', args=[self.deal.pk])
        )

        self.assertContains(response, 'Deal Contact')
        self.assertContains(response, 'Second Contact')
        self.assertNotContains(response, 'Other User Contact')
        for form_response in (response, edit_response):
            with self.subTest(path=form_response.request['PATH_INFO']):
                form = form_response.context['form']
                self.assertEqual(
                    set(form.fields['contact'].queryset.values_list('pk', flat=True)),
                    {self.contact.pk, self.second_contact.pk},
                )

    def test_tampered_post_with_another_users_contact_is_rejected(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('deal_add'),
            self.valid_data(contact=self.other_contact.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Deal.objects.filter(title='New Deal').exists())

        edit_response = self.client.post(
            reverse('deal_edit', args=[self.deal.pk]),
            self.valid_data(contact=self.other_contact.pk),
        )
        self.assertEqual(edit_response.status_code, 200)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.contact, self.contact)

    def test_create_preselects_contact_and_shows_success_message(self):
        self.client.force_login(self.owner)

        form_response = self.client.get(
            f"{reverse('deal_add')}?contact={self.second_contact.pk}"
        )
        self.assertEqual(
            int(form_response.context['form']['contact'].value()),
            self.second_contact.pk,
        )
        response = self.client.post(
            reverse('deal_add'),
            self.valid_data(),
        )

        self.assertRedirects(response, reverse('deal_list'))
        created_deal = Deal.objects.get(title='New Deal')
        self.assertEqual(created_deal.contact, self.contact)
        self.assertIn('Deal created successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])

    def test_create_rejects_negative_value_and_invalid_stage(self):
        self.client.force_login(self.owner)

        negative_response = self.client.post(
            reverse('deal_add'),
            self.valid_data(value='-1'),
        )
        self.assertEqual(negative_response.status_code, 200)
        self.assertFalse(Deal.objects.filter(title='New Deal').exists())

        invalid_stage_response = self.client.post(
            reverse('deal_add'),
            self.valid_data(stage='unknown'),
        )
        self.assertEqual(invalid_stage_response.status_code, 200)
        self.assertFalse(Deal.objects.filter(title='New Deal').exists())

    def test_update_deal_and_show_success_message(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('deal_edit', args=[self.deal.pk]),
            self.valid_data(
                contact=self.second_contact.pk,
                title='Updated Deal',
                stage='won',
                value='2500.00',
                currency='EUR',
            ),
        )

        self.assertRedirects(response, reverse('deal_list'))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.title, 'Updated Deal')
        self.assertEqual(self.deal.contact, self.second_contact)
        self.assertEqual(self.deal.stage, 'won')
        self.assertIn('Deal updated successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])

    def test_delete_deal_and_show_success_message(self):
        self.client.force_login(self.owner)

        confirmation = self.client.get(reverse('deal_delete', args=[self.deal.pk]))
        self.assertContains(confirmation, 'Existing Deal')
        response = self.client.post(reverse('deal_delete', args=[self.deal.pk]))

        self.assertRedirects(response, reverse('deal_list'))
        self.assertFalse(Deal.objects.filter(pk=self.deal.pk).exists())
        self.assertIn('Deal deleted successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])

    def test_other_users_deal_cannot_be_viewed_edited_or_deleted(self):
        other_deal = Deal.objects.create(
            contact=self.other_contact,
            title='Private Deal',
            value=999,
        )
        self.client.force_login(self.owner)

        for route in ('deal_detail', 'deal_edit', 'deal_delete'):
            url = reverse(route, args=[other_deal.pk])
            with self.subTest(route=route):
                self.assertEqual(self.client.get(url).status_code, 404)
                if route == 'deal_detail':
                    continue
                self.assertEqual(
                    self.client.post(url, self.valid_data()).status_code,
                    404,
                )
        other_deal.refresh_from_db()
        self.assertEqual(other_deal.title, 'Private Deal')

    def test_list_empty_states_and_dashboard_deal_count_remain_correct(self):
        self.client.force_login(self.owner)
        self.deal.delete()

        empty_list = self.client.get(reverse('deal_list'))
        self.assertContains(empty_list, 'No deals yet')
        self.assertContains(empty_list, 'Add your first deal')

        created = self.client.post(reverse('deal_add'), self.valid_data())
        self.assertRedirects(created, reverse('deal_list'))
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.context['total_deals'], 1)

        Deal.objects.filter(contact__owner=self.owner).delete()
        empty_contacts_user = User.objects.create_user(
            username='no-contacts',
            email='no-contacts@example.com',
            password='StrongPassword!248',
        )
        self.client.force_login(empty_contacts_user)
        no_contacts_list = self.client.get(reverse('deal_list'))
        self.assertContains(no_contacts_list, 'No deals yet')
        self.assertContains(no_contacts_list, 'Add your first deal')
        self.assertContains(no_contacts_list, 'Add a contact before creating a deal.')
        self.assertContains(no_contacts_list, reverse('contact_add'))


class DealKanbanTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='board-owner',
            email='board-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='board-other',
            email='board-other@example.com',
            password='AnotherPassword!248',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Board Contact',
            email='board-contact@example.com',
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Private Board Contact',
            email='private-board@example.com',
        )
        self.deal = Deal.objects.create(
            contact=self.contact,
            title='Board Deal',
            stage='new',
            value=Decimal('1250'),
            currency='PKR',
        )
        self.other_deal = Deal.objects.create(
            contact=self.other_contact,
            title='Private Board Deal',
            stage='new',
            value=Decimal('9000'),
            currency='USD',
        )
        self.client.force_login(self.owner)

    def test_board_and_move_endpoint_require_login(self):
        self.client.logout()
        board_response = self.client.get(reverse('deal_board'))
        self.assertEqual(board_response.status_code, 302)
        move_response = self.client.post(
            reverse('deal_move', args=[self.deal.pk]),
            {'stage': 'won'},
        )
        self.assertEqual(move_response.status_code, 302)

    def test_move_endpoint_is_owner_scoped_and_rejects_bad_methods_and_stages(self):
        move_url = reverse('deal_move', args=[self.deal.pk])
        self.assertEqual(self.client.get(move_url).status_code, 405)
        self.assertEqual(
            self.client.post(
                reverse('deal_move', args=[self.other_deal.pk]),
                {'stage': 'won'},
            ).status_code,
            404,
        )
        invalid = self.client.post(move_url, {'stage': 'not-a-stage'})
        self.assertEqual(invalid.status_code, 400)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.stage, 'new')
        self.assertFalse(DealStageHistory.objects.filter(deal=self.deal).exists())

    def test_move_endpoint_requires_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        move_url = reverse('deal_move', args=[self.deal.pk])
        self.assertEqual(client.post(move_url, {'stage': 'won'}).status_code, 403)

        board_response = client.get(reverse('deal_board'))
        token_match = re.search(
            rb'name="csrfmiddlewaretoken" value="([^"]+)"',
            board_response.content,
        )
        self.assertIsNotNone(token_match)
        token = token_match.group(1).decode()
        response = client.post(
            move_url,
            {'stage': 'won'},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)

    def test_move_updates_stage_timestamp_and_creates_history_once(self):
        previous_timestamp = timezone.now() - timedelta(days=5)
        Deal.objects.filter(pk=self.deal.pk).update(
            stage_changed_at=previous_timestamp,
        )
        response = self.client.post(
            reverse('deal_move', args=[self.deal.pk]),
            {'stage': 'contacted'},
        )
        self.assertEqual(response.status_code, 200)
        self.deal.refresh_from_db()
        history = DealStageHistory.objects.get(deal=self.deal)
        self.assertEqual(self.deal.stage, 'contacted')
        self.assertGreater(self.deal.stage_changed_at, previous_timestamp)
        self.assertEqual(history.from_stage, 'new')
        self.assertEqual(history.to_stage, 'contacted')
        self.assertEqual(history.changed_by, self.owner)

        updated_timestamp = self.deal.stage_changed_at
        response = self.client.post(
            reverse('deal_move', args=[self.deal.pk]),
            {'stage': 'contacted'},
        )
        self.assertEqual(response.status_code, 200)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.stage_changed_at, updated_timestamp)
        self.assertEqual(DealStageHistory.objects.filter(deal=self.deal).count(), 1)

    def test_stage_change_from_edit_form_records_history(self):
        response = self.client.post(
            reverse('deal_edit', args=[self.deal.pk]),
            {
                'contact': self.contact.pk,
                'title': self.deal.title,
                'stage': 'won',
                'value': '1250',
                'currency': 'PKR',
            },
        )
        self.assertRedirects(response, reverse('deal_list'))
        self.deal.refresh_from_db()
        history = DealStageHistory.objects.get(deal=self.deal)
        self.assertEqual(self.deal.stage, 'won')
        self.assertEqual((history.from_stage, history.to_stage), ('new', 'won'))
        self.assertEqual(history.changed_by, self.owner)

    def test_board_filters_owner_deals_and_groups_totals_by_currency(self):
        Deal.objects.create(
            contact=self.contact,
            title='USD Board Deal',
            stage='new',
            value=Decimal('300'),
            currency='USD',
        )
        columns = build_deal_board(self.owner)
        self.assertEqual([column['value'] for column in columns], [
            stage for stage, _ in Deal.STAGE_CHOICES
        ])
        new_column = columns[0]
        self.assertEqual(new_column['count'], 2)
        self.assertEqual(new_column['totals'], [
            {'currency': 'PKR', 'value': Decimal('1250.00')},
            {'currency': 'USD', 'value': Decimal('300.00')},
        ])

        response = self.client.get(reverse('deal_board'))
        self.assertContains(response, 'Board Deal')
        self.assertNotContains(response, 'Private Board Deal')
        self.assertContains(response, 'Rs 1,250')
        self.assertContains(response, '$300')

    def test_board_search_currency_filter_and_overdue_task_indicator(self):
        from .models import Task

        Task.objects.create(
            owner=self.owner,
            deal=self.deal,
            contact=self.contact,
            title='Overdue board task',
            due_date=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(
            reverse('deal_board'),
            {'q': 'Board Deal', 'currency': 'PKR'},
        )
        self.assertContains(response, 'Board Deal')
        self.assertContains(response, 'Has overdue pending tasks')
        self.assertNotContains(response, 'USD Board Deal')
        self.assertNotContains(response, 'Private Board Deal')

    def test_quick_add_preselects_valid_stage(self):
        response = self.client.get(
            reverse('deal_add'),
            {'stage': 'negotiation'},
        )
        self.assertEqual(
            response.context['form']['stage'].value(),
            'negotiation',
        )
        invalid = self.client.get(reverse('deal_add'), {'stage': 'bogus'})
        self.assertEqual(invalid.context['form']['stage'].value(), 'new')


class ListFilteringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='list-owner',
            email='list-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='list-other',
            email='list-other@example.com',
            password='AnotherPassword!248',
        )
        self.contacts = []
        stages = [stage for stage, _ in Deal.STAGE_CHOICES]
        currencies = [currency for currency, _ in Deal.CURRENCY_CHOICES]
        for index in range(12):
            contact = Contact.objects.create(
                owner=self.owner,
                name=f'Customer {index:02}',
                email=f'customer{index:02}@example.com',
                phone=f'555-01{index:02}',
                company=Company.objects.create(
                    owner=self.owner,
                    name=f'Company {index:02}',
                ),
            )
            self.contacts.append(contact)
            Deal.objects.create(
                contact=contact,
                title=f'Opportunity {index:02}',
                stage=stages[index % len(stages)],
                value=100 + index * 10,
                currency=currencies[index % len(currencies)],
            )
        for index in range(10):
            Deal.objects.create(
                contact=self.contacts[index % len(self.contacts)],
                title=f'Opportunity Extra {index:02}',
                stage='won',
                value=1000 + index * 10,
                currency='PKR',
            )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Private Exact Search Name',
            email='private@example.com',
        )
        Deal.objects.create(
            contact=self.other_contact,
            title='Private Exact Opportunity',
            value=5000,
            currency='USD',
        )
        self.client.force_login(self.owner)

    def test_contact_search_matches_all_requested_fields(self):
        for query in (
            'Customer 03',
            'CUSTOMER03@EXAMPLE.COM',
            '555-0103',
            'Company 03',
        ):
            with self.subTest(query=query):
                response = self.client.get(reverse('contact_list'), {'q': query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['paginator'].count, 1)
                self.assertContains(response, 'Customer 03')

    def test_contact_sort_choices_and_invalid_sort_are_safe(self):
        expected_first_names = {
            '': 'Customer 11',
            'oldest': 'Customer 00',
            'name_asc': 'Customer 00',
            'name_desc': 'Customer 11',
        }
        for sort, expected_name in expected_first_names.items():
            with self.subTest(sort=sort):
                response = self.client.get(
                    reverse('contact_list'),
                    {'sort': sort},
                )
                self.assertEqual(
                    response.context['page_obj'].object_list.first().name,
                    expected_name,
                )

        response = self.client.get(
            reverse('contact_list'),
            {'sort': 'name_asc'},
        )
        self.assertEqual(
            list(response.context['page_obj'].object_list.values_list('name', flat=True)),
            [f'Customer {index:02}' for index in range(10)],
        )

        response = self.client.get(
            reverse('contact_list'),
            {'sort': 'not-allowed'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['paginator'].count, 12)

    def test_contact_pagination_has_ten_items_and_handles_invalid_pages(self):
        response = self.client.get(reverse('contact_list'), {'page': '1'})
        self.assertEqual(len(response.context['page_obj'].object_list), 10)
        self.assertEqual(response.context['paginator'].count, 12)

        invalid_page = self.client.get(
            reverse('contact_list'),
            {'page': 'not-a-number'},
        )
        self.assertEqual(invalid_page.status_code, 200)
        self.assertEqual(invalid_page.context['page_obj'].number, 1)

    def test_deal_search_matches_title_and_contact_name(self):
        title_results = self.client.get(
            reverse('deal_list'),
            {'q': 'Opportunity 03'},
        )
        contact_results = self.client.get(
            reverse('deal_list'),
            {'q': 'Customer 04'},
        )
        self.assertEqual(title_results.context['paginator'].count, 1)
        self.assertContains(title_results, 'Opportunity 03')
        self.assertEqual(contact_results.context['paginator'].count, 2)
        self.assertContains(contact_results, 'Customer 04')
        self.assertContains(contact_results, 'Opportunity Extra 04')

    def test_deal_stage_currency_and_value_filters_work(self):
        stage_response = self.client.get(
            reverse('deal_list'),
            {'stage': 'won'},
        )
        self.assertTrue(all(
            deal.stage == 'won'
            for deal in stage_response.context['page_obj'].object_list
        ))

        currency_response = self.client.get(
            reverse('deal_list'),
            {'currency': 'USD'},
        )
        self.assertTrue(all(
            deal.currency == 'USD'
            for deal in currency_response.context['page_obj'].object_list
        ))

        range_response = self.client.get(
            reverse('deal_list'),
            {'min_value': '150', 'max_value': '170'},
        )
        self.assertEqual(
            list(range_response.context['page_obj'].object_list.values_list('value', flat=True)),
            [Decimal('170.00'), Decimal('160.00'), Decimal('150.00')],
        )
        min_response = self.client.get(
            reverse('deal_list'),
            {'min_value': '210'},
        )
        self.assertTrue(all(
            deal.value >= Decimal('210')
            for deal in min_response.context['page_obj'].object_list
        ))
        max_response = self.client.get(
            reverse('deal_list'),
            {'max_value': '120'},
        )
        self.assertTrue(all(
            deal.value <= Decimal('120')
            for deal in max_response.context['page_obj'].object_list
        ))

    def test_deal_sort_and_filtered_currency_summary(self):
        response = self.client.get(
            reverse('deal_list'),
            {'sort': 'value_desc', 'currency': 'USD'},
        )
        values = list(
            response.context['page_obj'].object_list.values_list('value', flat=True)
        )
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertTrue(all(
            deal.currency == 'USD'
            for deal in response.context['page_obj'].object_list
        ))
        self.assertEqual(
            list(response.context['filtered_value_totals']),
            [{
                'currency': 'USD',
                'total': sum(
                    (Decimal(100 + index * 10) for index in range(12)
                     if Deal.CURRENCY_CHOICES[index % len(Deal.CURRENCY_CHOICES)][0] == 'USD'),
                    Decimal('0'),
                ),
            }],
        )
        all_currency_totals = self.client.get(reverse('deal_list')).context[
            'filtered_value_totals'
        ]
        self.assertEqual(
            {item['currency'] for item in all_currency_totals},
            {currency for currency, _ in Deal.CURRENCY_CHOICES},
        )

        title_response = self.client.get(
            reverse('deal_list'),
            {'sort': 'title_asc'},
        )
        self.assertEqual(
            title_response.context['page_obj'].object_list.first().title,
            'Opportunity 00',
        )
        value_asc_response = self.client.get(
            reverse('deal_list'),
            {'sort': 'value_asc'},
        )
        sorted_values = list(
            value_asc_response.context['page_obj'].object_list.values_list('value', flat=True)
        )
        self.assertEqual(sorted_values, sorted(sorted_values))

    def test_invalid_deal_sort_values_and_page_numbers_do_not_crash(self):
        for params in (
            {'sort': 'not-allowed'},
            {'page': 'not-a-number'},
            {'page': '9999'},
            {'min_value': 'not-a-number'},
            {'max_value': 'invalid'},
        ):
            with self.subTest(params=params):
                response = self.client.get(reverse('deal_list'), params)
                self.assertEqual(response.status_code, 200)

    def test_pagination_keeps_search_filter_and_sort_parameters(self):
        response = self.client.get(
            reverse('deal_list'),
            {'q': 'Opportunity', 'stage': 'won', 'sort': 'title_asc'},
        )
        self.assertContains(response, 'Showing 1-10 of 12 results')
        self.assertContains(response, 'title_asc')

        all_deals = self.client.get(
            reverse('deal_list'),
            {'q': 'Opportunity', 'stage': 'won', 'sort': 'title_asc'},
        )
        page_links = re.findall(r'href="([^"]+)"[^>]*>2</a>', all_deals.content.decode())
        self.assertTrue(page_links)
        params = parse_qs(urlsplit(unescape(page_links[0])).query)
        self.assertEqual(params['q'], ['Opportunity'])
        self.assertEqual(params['stage'], ['won'])
        self.assertEqual(params['sort'], ['title_asc'])
        self.assertEqual(params['page'], ['2'])

    def test_search_does_not_expose_another_users_records(self):
        self.client.force_login(self.other_user)
        response = self.client.get(
            reverse('contact_list'),
            {'q': 'Customer 03'},
        )
        self.assertEqual(response.context['paginator'].count, 0)
        self.assertNotContains(response, f'href="/contacts/{self.contacts[3].pk}/"')

        response = self.client.get(
            reverse('deal_list'),
            {'q': 'Opportunity 03'},
        )
        self.assertEqual(response.context['paginator'].count, 0)
        owned_deal = Deal.objects.get(title='Opportunity 03')
        self.assertNotContains(response, f'href="/deals/{owned_deal.pk}/"')


class NoteTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='note-owner',
            email='note-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='note-other',
            email='note-other@example.com',
            password='AnotherPassword!248',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Note Contact',
            email='note-contact@example.com',
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Other Note Contact',
            email='other-note-contact@example.com',
        )
        self.deal = Deal.objects.create(
            contact=self.contact,
            title='Note Deal',
            value=100,
        )
        self.other_deal = Deal.objects.create(
            contact=self.other_contact,
            title='Other Note Deal',
            value=200,
        )

    def test_note_pages_require_login(self):
        note = Note.objects.create(
            owner=self.owner,
            contact=self.contact,
            content='Existing note',
        )
        urls = [
            reverse('contact_note_add', args=[self.contact.pk]),
            reverse('deal_note_add', args=[self.deal.pk]),
            reverse('note_edit', args=[note.pk]),
            reverse('note_delete', args=[note.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f'{reverse("login")}?next={url}')

    def test_create_note_from_contact_detail(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('contact_note_add', args=[self.contact.pk]),
            {'content': 'Call customer\nSend proposal'},
        )

        self.assertRedirects(
            response,
            reverse('contact_detail', args=[self.contact.pk]),
        )
        note = Note.objects.get()
        self.assertEqual(note.owner, self.owner)
        self.assertEqual(note.contact, self.contact)
        self.assertIsNone(note.deal)
        self.assertIn('Note added successfully.', [
            str(message) for message in response.wsgi_request._messages
        ])
        detail = self.client.get(reverse('contact_detail', args=[self.contact.pk]))
        self.assertContains(detail, 'Notes')
        self.assertContains(detail, '1')
        self.assertContains(detail, 'Call customer')
        self.assertContains(detail, 'Send proposal')

    def test_create_note_from_deal_detail_links_deal_and_contact(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('deal_note_add', args=[self.deal.pk]),
            {'content': 'Discussed pricing'},
        )

        self.assertRedirects(response, reverse('deal_detail', args=[self.deal.pk]))
        note = Note.objects.get()
        self.assertEqual(note.owner, self.owner)
        self.assertEqual(note.contact, self.contact)
        self.assertEqual(note.deal, self.deal)
        self.assertContains(
            self.client.get(reverse('deal_detail', args=[self.deal.pk])),
            'Discussed pricing',
        )

    def test_tampered_note_associations_are_rejected(self):
        self.client.force_login(self.owner)

        contact_response = self.client.post(
            reverse('contact_note_add', args=[self.contact.pk]),
            {
                'content': 'Tampered contact note',
                'contact': self.other_contact.pk,
            },
        )
        self.assertEqual(contact_response.status_code, 200)
        self.assertFalse(Note.objects.filter(content='Tampered contact note').exists())

        deal_response = self.client.post(
            reverse('deal_note_add', args=[self.deal.pk]),
            {
                'content': 'Tampered deal note',
                'deal_id': self.other_deal.pk,
            },
        )
        self.assertEqual(deal_response.status_code, 200)
        self.assertFalse(Note.objects.filter(content='Tampered deal note').exists())

        self.assertEqual(
            self.client.post(
                reverse('contact_note_add', args=[self.other_contact.pk]),
                {'content': 'Private contact note'},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('deal_note_add', args=[self.other_deal.pk]),
                {'content': 'Private deal note'},
            ).status_code,
            404,
        )

    def test_other_users_cannot_view_edit_or_delete_notes(self):
        note = Note.objects.create(
            owner=self.other_user,
            contact=self.other_contact,
            content='Private note',
            deal=self.other_deal,
        )
        self.client.force_login(self.owner)

        self.assertNotContains(
            self.client.get(reverse('contact_detail', args=[self.contact.pk])),
            'Private note',
        )
        for route in ('note_edit', 'note_delete'):
            url = reverse(route, args=[note.pk])
            with self.subTest(route=route):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(
                    self.client.post(url, {'content': 'Changed'}).status_code,
                    404,
                )
        note.refresh_from_db()
        self.assertEqual(note.content, 'Private note')

    def test_edit_and_delete_note_return_to_origin_page(self):
        note = Note.objects.create(
            owner=self.owner,
            contact=self.contact,
            deal=self.deal,
            content='Original note',
        )
        self.client.force_login(self.owner)
        deal_detail = reverse('deal_detail', args=[self.deal.pk])

        update_response = self.client.post(
            f"{reverse('note_edit', args=[note.pk])}?next={deal_detail}",
            {'content': 'Updated note', 'next': deal_detail},
        )
        self.assertRedirects(update_response, deal_detail)
        note.refresh_from_db()
        self.assertEqual(note.content, 'Updated note')
        self.assertIn('Note updated successfully.', [
            str(message) for message in update_response.wsgi_request._messages
        ])

        delete_response = self.client.post(
            f"{reverse('note_delete', args=[note.pk])}?next={deal_detail}",
            {'next': deal_detail},
        )
        self.assertRedirects(delete_response, deal_detail)
        self.assertFalse(Note.objects.filter(pk=note.pk).exists())
        self.assertIn('Note deleted successfully.', [
            str(message) for message in delete_response.wsgi_request._messages
        ])

    def test_deleting_linked_deal_preserves_note_on_contact(self):
        note = Note.objects.create(
            owner=self.owner,
            contact=self.contact,
            deal=self.deal,
            content='Keep this note',
        )

        self.deal.delete()
        note.refresh_from_db()

        self.assertIsNone(note.deal)
        self.assertEqual(note.contact, self.contact)


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='StrongPassword!248',
        )

    def test_signup_creates_user_and_signs_them_in(self):
        response = self.client.post(reverse('signup'), {
            'username': 'newowner',
            'email': 'newowner@example.com',
            'password1': 'StrongPassword!248',
            'password2': 'StrongPassword!248',
        })

        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(
            User.objects.filter(username='newowner', email='newowner@example.com').exists()
        )
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.wsgi_request.user.username, 'newowner')

    def test_signup_rejects_email_already_in_use(self):
        response = self.client.post(reverse('signup'), {
            'username': 'another',
            'email': 'OWNER@example.com',
            'password1': 'StrongPassword!248',
            'password2': 'StrongPassword!248',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'An account with this email already exists.')
        self.assertFalse(User.objects.filter(username='another').exists())

    def test_login_accepts_username_or_email(self):
        for username in ('owner', 'owner@example.com'):
            with self.subTest(username=username):
                self.client.logout()
                response = self.client.post(reverse('login'), {
                    'username': username,
                    'password': 'StrongPassword!248',
                })
                self.assertRedirects(response, reverse('dashboard'))
                dashboard = self.client.get(reverse('dashboard'))
                self.assertEqual(dashboard.wsgi_request.user.pk, self.user.pk)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_email_changes_password(self):
        response = self.client.post(reverse('password_reset'), {
            'email': 'owner@example.com',
        })

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['owner@example.com'])
        self.assertIn(
            'http://testserver/reset/',
            mail.outbox[0].body,
        )

        reset_url = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if line.startswith('http://testserver/reset/')
        )
        response = self.client.get(reset_url, follow=True)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(response.wsgi_request.path, {
            'new_password1': 'NewStrongPassword!248',
            'new_password2': 'NewStrongPassword!248',
        })

        self.assertRedirects(response, reverse('password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewStrongPassword!248'))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_response_does_not_reveal_unknown_email(self):
        response = self.client.post(reverse('password_reset'), {
            'email': 'unknown@example.com',
        })

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class AccountDataIsolationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='AnotherPassword!248',
        )
        self.own_contact = Contact.objects.create(
            owner=self.owner,
            name='Own contact',
            email='own-contact@example.com',
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Other contact',
            email='other-contact@example.com',
        )
        self.own_deal = Deal.objects.create(
            contact=self.own_contact,
            title='Own deal',
            value=125,
        )
        self.other_deal = Deal.objects.create(
            contact=self.other_contact,
            title='Other deal',
            value=500,
        )
        self.client.force_login(self.owner)

    def test_dashboard_and_lists_show_only_current_users_records(self):
        dashboard = self.client.get(reverse('dashboard'))
        contacts = self.client.get(reverse('contact_list'))
        deals = self.client.get(reverse('deal_list'))

        self.assertEqual(dashboard.context['total_contacts'], 1)
        self.assertEqual(dashboard.context['total_deals'], 1)
        self.assertContains(contacts, 'Own contact')
        self.assertNotContains(contacts, 'Other contact')
        self.assertContains(deals, 'Own deal')
        self.assertNotContains(deals, 'Other deal')
        self.assertNotContains(dashboard, '/admin/')

    def test_dashboard_admin_links_are_visible_only_to_staff(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])

        dashboard = self.client.get(reverse('dashboard'))

        self.assertContains(dashboard, '/admin/')
        self.assertContains(dashboard, 'href="/admin/">Admin</a>')

    def test_dashboard_stage_table_and_chart_share_ordered_complete_stages(self):
        Deal.objects.create(
            contact=self.own_contact,
            title='Negotiation deal',
            stage='negotiation',
        )
        Deal.objects.create(
            contact=self.own_contact,
            title='Lost deal',
            stage='lost',
        )

        response = self.client.get(reverse('dashboard'))
        expected_rows = [
            {'value': 'new', 'label': 'New', 'count': 1},
            {'value': 'contacted', 'label': 'Contacted', 'count': 0},
            {'value': 'negotiation', 'label': 'Negotiation', 'count': 1},
            {'value': 'won', 'label': 'Won', 'count': 0},
            {'value': 'lost', 'label': 'Lost', 'count': 1},
        ]

        self.assertEqual(response.context['stage_rows'], expected_rows)
        self.assertEqual(
            json.loads(response.context['chart_labels']),
            [row['label'] for row in expected_rows],
        )
        self.assertEqual(
            json.loads(response.context['chart_data']),
            [row['count'] for row in expected_rows],
        )
        for row in expected_rows:
            self.assertContains(
                response,
                f">{row['label']}</span>",
            )

    def test_other_users_records_cannot_be_viewed_or_changed(self):
        self.assertEqual(
            self.client.get(
                reverse('contact_detail', args=[self.other_contact.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('contact_edit', args=[self.other_contact.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('deal_edit', args=[self.other_deal.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('deal_delete', args=[self.other_deal.pk])
            ).status_code,
            404,
        )

    def test_deal_form_rejects_another_users_contact(self):
        response = self.client.post(reverse('deal_add'), {
            'contact': self.other_contact.pk,
            'title': 'Invalid deal',
            'stage': 'new',
            'value': '10.00',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Deal.objects.filter(title='Invalid deal').exists())

    def test_deal_currency_defaults_to_pkr_and_is_available_in_form(self):
        self.assertEqual(self.own_deal.currency, 'PKR')
        form = DealForm(user=self.owner)
        self.assertIn('currency', form.fields)
        self.assertEqual(
            list(form.fields['currency'].choices),
            Deal.CURRENCY_CHOICES,
        )

    def test_display_currency_formats_values_by_currency(self):
        self.assertEqual(display_currency(1500, 'PKR'), 'Rs 1,500')
        self.assertEqual(display_currency(1500, 'USD'), '$1,500')
        self.assertEqual(display_currency(1500, 'AED'), 'AED 1,500')
        self.assertEqual(display_currency(1500.5, 'GBP'), '£1,500.5')
        self.assertEqual(display_currency(1500.25, 'EUR'), '€1,500.25')


class TaskTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='task-owner',
            email='task-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='task-other',
            email='task-other@example.com',
            password='AnotherPassword!248',
        )
        self.contact = Contact.objects.create(
            owner=self.owner,
            name='Task Contact',
            email='task-contact@example.com',
        )
        self.other_contact = Contact.objects.create(
            owner=self.other_user,
            name='Other Task Contact',
            email='other-task-contact@example.com',
        )
        self.deal = Deal.objects.create(
            contact=self.contact,
            title='Task Deal',
        )
        self.other_deal = Deal.objects.create(
            contact=self.other_contact,
            title='Other Task Deal',
        )
        self.task = Task.objects.create(
            owner=self.owner,
            title='Existing task',
            due_date=timezone.now() + timedelta(days=1),
            contact=self.contact,
        )
        self.other_task = Task.objects.create(
            owner=self.other_user,
            title='Private task',
            due_date=timezone.now() + timedelta(days=1),
            contact=self.other_contact,
        )

    def due_date_input(self, value):
        return timezone.localtime(value).strftime('%Y-%m-%dT%H:%M')

    def task_post_data(self, **overrides):
        data = {
            'title': 'Follow up',
            'description': 'Call the customer',
            'contact': '',
            'deal': '',
            'due_date': self.due_date_input(timezone.now() + timedelta(days=2)),
            'priority': 'medium',
            'status': 'pending',
        }
        data.update(overrides)
        return data

    def test_task_pages_require_login(self):
        urls = [
            reverse('task_list'),
            reverse('task_add'),
            reverse('task_edit', args=[self.task.pk]),
            reverse('task_delete', args=[self.task.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    302,
                )
                self.assertTrue(response.url.startswith(reverse('login')))

        response = self.client.post(
            reverse('task_toggle_complete', args=[self.task.pk]),
        )
        self.assertEqual(response.status_code, 302)

    def test_user_cannot_access_another_users_task(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(
                reverse('task_edit', args=[self.other_task.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('task_delete', args=[self.other_task.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('task_edit', args=[self.other_task.pk]),
                self.task_post_data(title='Hacked task'),
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('task_delete', args=[self.other_task.pk]),
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('task_toggle_complete', args=[self.other_task.pk]),
                {'next': reverse('task_list')},
            ).status_code,
            404,
        )
        response = self.client.get(reverse('task_list'))
        self.assertContains(response, 'Existing task')
        self.assertNotContains(response, 'Private task')

    def test_task_create_uses_owned_dropdowns_and_preselects_contact_or_deal(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('task_add'),
            {
                'deal': self.deal.pk,
                'next': reverse('deal_detail', args=[self.deal.pk]),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['form']['deal'].value(),
            self.deal.pk,
        )
        self.assertEqual(
            response.context['form']['contact'].value(),
            self.contact.pk,
        )
        self.assertContains(response, 'Task Contact')
        self.assertNotContains(response, 'Other Task Contact')

        post = self.task_post_data(
            contact=self.contact.pk,
            deal=self.deal.pk,
            next=reverse('deal_detail', args=[self.deal.pk]),
        )
        response = self.client.post(reverse('task_add'), post)
        self.assertRedirects(
            response,
            reverse('deal_detail', args=[self.deal.pk]),
        )
        created = Task.objects.get(title='Follow up')
        self.assertEqual(created.owner, self.owner)
        self.assertEqual(created.contact, self.contact)
        self.assertEqual(created.deal, self.deal)

    def test_task_form_rejects_tampered_foreign_contact_or_deal(self):
        self.client.force_login(self.owner)
        for field, foreign_record in (
            ('contact', self.other_contact),
            ('deal', self.other_deal),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    reverse('task_add'),
                    self.task_post_data(**{field: foreign_record.pk}),
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Task.objects.filter(title='Follow up').exists())

    def test_task_update_and_delete(self):
        self.client.force_login(self.owner)
        update_data = self.task_post_data(
            title='Updated task',
            contact=self.contact.pk,
            status='completed',
        )
        response = self.client.post(
            reverse('task_edit', args=[self.task.pk]),
            update_data,
        )
        self.assertRedirects(response, reverse('task_list'))
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Updated task')
        self.assertEqual(self.task.status, 'completed')
        self.assertIsNotNone(self.task.completed_at)

        response = self.client.post(
            reverse('task_delete', args=[self.task.pk]),
            {'next': reverse('task_list')},
        )
        self.assertRedirects(response, reverse('task_list'))
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())

    def test_mark_complete_and_reopen(self):
        self.client.force_login(self.owner)
        toggle_url = reverse('task_toggle_complete', args=[self.task.pk])
        response = self.client.post(toggle_url, {'next': reverse('task_list')})
        self.assertRedirects(response, reverse('task_list'))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'completed')
        self.assertIsNotNone(self.task.completed_at)

        self.client.post(toggle_url, {'next': reverse('task_list')})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')
        self.assertIsNone(self.task.completed_at)

    def test_overdue_property_and_task_tabs(self):
        overdue = Task.objects.create(
            owner=self.owner,
            title='Overdue task',
            due_date=timezone.now() - timedelta(days=1, hours=1),
        )
        today_start, today_end = self._get_local_day_bounds()
        today = Task.objects.create(
            owner=self.owner,
            title='Today task',
            due_date=today_start + (today_end - today_start) / 2,
        )
        completed = Task.objects.create(
            owner=self.owner,
            title='Completed task',
            due_date=timezone.now() - timedelta(days=2),
            status='completed',
            completed_at=timezone.now(),
        )
        self.assertTrue(overdue.is_overdue)
        self.assertFalse(completed.is_overdue)
        self.assertFalse(self.task.is_overdue)

        self.client.force_login(self.owner)
        overdue_expected = {'Overdue task'}
        if today.is_overdue:
            overdue_expected.add('Today task')
        for tab, expected in (
            ('overdue', overdue_expected),
            ('today', {'Today task'}),
            ('completed', {'Completed task'}),
        ):
            with self.subTest(tab=tab):
                response = self.client.get(reverse('task_list'), {'tab': tab})
                shown = set(
                    response.context['page_obj'].object_list.values_list(
                        'title',
                        flat=True,
                    )
                )
                self.assertEqual(shown, expected)

    @staticmethod
    def _get_local_day_bounds():
        from .services import local_day_bounds
        return local_day_bounds()

    def test_dashboard_widget_shows_only_owned_due_today_or_overdue_tasks(self):
        overdue = Task.objects.create(
            owner=self.owner,
            title='Dashboard overdue',
            due_date=timezone.now() - timedelta(days=1, hours=1),
        )
        today_start, today_end = self._get_local_day_bounds()
        today = Task.objects.create(
            owner=self.owner,
            title='Dashboard today',
            due_date=today_start + (today_end - today_start) / 2,
        )
        future = Task.objects.create(
            owner=self.owner,
            title='Dashboard future',
            due_date=today_end + timedelta(days=1),
        )
        other = Task.objects.create(
            owner=self.other_user,
            title='Dashboard private',
            due_date=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, overdue.title)
        self.assertContains(response, today.title)
        self.assertNotContains(response, future.title)
        self.assertNotContains(response, other.title)
        self.assertEqual(
            list(response.context['dashboard_tasks']),
            [overdue, today],
        )
        self.assertEqual(response.context['overdue_task_count'], 2)


class LeadTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='lead-owner',
            email='lead-owner@example.com',
            password='StrongPassword!248',
        )
        self.other_user = User.objects.create_user(
            username='lead-other',
            email='lead-other@example.com',
            password='AnotherPassword!248',
        )
        self.lead = Lead.objects.create(
            owner=self.owner,
            name='Alice Example',
            email='alice@example.com',
            phone='+92 300 1234567',
            company_name='Example Co',
            job_title='Director',
            source='referral',
            status='qualified',
            estimated_value=Decimal('1250.50'),
            currency='PKR',
            notes='Discussed annual plan.',
        )

    def lead_post_data(self, **overrides):
        data = {
            'name': 'Alice Example',
            'email': 'alice@example.com',
            'phone': '+92 300 1234567',
            'company_name': 'Example Co',
            'job_title': 'Director',
            'source': 'referral',
            'status': 'qualified',
            'estimated_value': '1250.50',
            'currency': 'PKR',
            'notes': 'Discussed annual plan.',
        }
        data.update(overrides)
        return data

    def test_lead_pages_and_mutations_require_login(self):
        urls = (
            reverse('lead_list'),
            reverse('lead_add'),
            reverse('lead_detail', args=[self.lead.pk]),
            reverse('lead_edit', args=[self.lead.pk]),
            reverse('lead_delete', args=[self.lead.pk]),
            reverse('lead_convert', args=[self.lead.pk]),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse('login')))

        response = self.client.post(
            reverse('lead_status', args=[self.lead.pk]),
            {'status': 'contacted'},
        )
        self.assertEqual(response.status_code, 302)

    def test_lead_owner_isolation(self):
        private = Lead.objects.create(owner=self.other_user, name='Private Lead')
        self.client.force_login(self.owner)

        for url in (
            reverse('lead_detail', args=[private.pk]),
            reverse('lead_edit', args=[private.pk]),
            reverse('lead_delete', args=[private.pk]),
            reverse('lead_convert', args=[private.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

        response = self.client.post(
            reverse('lead_status', args=[private.pk]),
            {'status': 'contacted'},
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse('lead_list'))
        self.assertContains(response, self.lead.name)
        self.assertNotContains(response, private.name)

    def test_create_edit_and_delete_lead(self):
        self.client.force_login(self.owner)
        created = self.client.post(
            reverse('lead_add'),
            self.lead_post_data(name='New Lead', email='new@example.com'),
        )
        self.assertRedirects(created, reverse('lead_list'))
        lead = Lead.objects.get(name='New Lead')
        self.assertEqual(lead.owner, self.owner)

        updated = self.client.post(
            reverse('lead_edit', args=[lead.pk]),
            self.lead_post_data(name='Updated Lead', email='updated@example.com'),
        )
        self.assertRedirects(updated, reverse('lead_list'))
        lead.refresh_from_db()
        self.assertEqual(lead.name, 'Updated Lead')

        deleted = self.client.post(reverse('lead_delete', args=[lead.pk]))
        self.assertRedirects(deleted, reverse('lead_list'))
        self.assertFalse(Lead.objects.filter(pk=lead.pk).exists())

    def test_list_search_filters_sort_and_pagination(self):
        Lead.objects.create(
            owner=self.owner,
            name='Aardvark Prospect',
            email='aardvark@example.com',
            company_name='Search Ltd',
            source='website',
            estimated_value=100,
        )
        Lead.objects.create(
            owner=self.owner,
            name='Website Other',
            email='other@example.com',
            source='website',
            status='unqualified',
            estimated_value=500,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse('lead_list'), {
            'q': 'Aardvark',
            'source': 'website',
            'sort': 'value_desc',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aardvark Prospect')
        self.assertNotContains(response, 'Alice Example')

        response = self.client.get(reverse('lead_list'), {'status': 'unqualified'})
        self.assertContains(response, 'Website Other')
        self.assertNotContains(response, 'Alice Example')

        Lead.objects.filter(owner=self.owner).delete()
        for index in range(12):
            Lead.objects.create(owner=self.owner, name=f'Lead {index}')
        response = self.client.get(reverse('lead_list'), {'page': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['leads']), 2)

    def test_status_change_rejects_tampered_values_and_allows_post(self):
        self.client.force_login(self.owner)
        url = reverse('lead_status', args=[self.lead.pk])
        response = self.client.post(url, {'status': 'converted'})
        self.assertEqual(response.status_code, 400)
        self.client.get(url)
        self.assertEqual(response.status_code, 400)

        response = self.client.post(url, {'status': 'contacted'})
        self.assertRedirects(response, reverse('lead_detail', args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'contacted')

    def test_conversion_creates_contact_company_deal_note_and_history(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('lead_convert', args=[self.lead.pk]), {
            'create_deal': 'on',
            'create_company': 'on',
            'deal_title': 'Enterprise opportunity',
            'deal_value': '1500.25',
            'deal_stage': 'negotiation',
        })
        contact = Contact.objects.get(email='alice@example.com')
        self.assertRedirects(response, reverse('contact_detail', args=[contact.pk]))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'converted')
        self.assertEqual(self.lead.converted_contact, contact)
        self.assertIsNotNone(self.lead.converted_at)
        self.assertEqual(contact.source, 'referral')
        self.assertEqual(contact.company.name, 'Example Co')
        deal = Deal.objects.get(contact=contact)
        self.assertEqual(deal.title, 'Enterprise opportunity')
        self.assertEqual(deal.value, Decimal('1500.25'))
        self.assertEqual(deal.stage, 'negotiation')
        self.assertEqual(deal.currency, 'PKR')
        self.assertTrue(Note.objects.filter(
            owner=self.owner,
            contact=contact,
            deal=deal,
            content='Discussed annual plan.',
        ).exists())
        self.assertTrue(DealStageHistory.objects.filter(
            deal=deal,
            from_stage='negotiation',
            to_stage='negotiation',
            changed_by=self.owner,
        ).exists())

    def test_conversion_without_deal_creates_contact_and_company_only(self):
        result = convert_lead(
            self.lead,
            self.owner,
            create_deal=False,
            company_option=True,
        )
        self.assertIsInstance(result.contact, Contact)
        self.assertIsInstance(result.company, Company)
        self.assertIsNone(result.deal)
        self.assertFalse(result.linked_existing_contact)
        self.assertFalse(Deal.objects.filter(contact=result.contact).exists())

    def test_existing_email_links_without_duplicate_and_reuses_company_ci(self):
        Company.objects.create(owner=self.owner, name='example co')
        existing = Contact.objects.create(
            owner=self.owner,
            name='Existing Alice',
            email='ALICE@example.com',
        )
        result = convert_lead(self.lead, self.owner, create_deal=False)
        self.assertTrue(result.linked_existing_contact)
        self.assertEqual(result.contact, existing)
        self.assertEqual(Contact.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(result.company.name, 'example co')
        existing.refresh_from_db()
        self.assertEqual(existing.company, result.company)

    def test_second_conversion_and_another_users_conversion_are_rejected(self):
        convert_lead(self.lead, self.owner, create_deal=False)
        with self.assertRaises(ValidationError):
            convert_lead(self.lead, self.owner, create_deal=False)

        other_lead = Lead.objects.create(owner=self.other_user, name='Other')
        with self.assertRaises(ValidationError):
            convert_lead(other_lead, self.owner, create_deal=False)

        self.client.force_login(self.owner)
        response = self.client.post(reverse('lead_convert', args=[self.lead.pk]), {})
        self.assertRedirects(response, reverse('lead_detail', args=[self.lead.pk]))

    def test_conversion_rolls_back_every_object_if_late_error_occurs(self):
        contact_count = Contact.objects.count()
        company_count = Company.objects.count()
        deal_count = Deal.objects.count()
        note_count = Note.objects.count()

        with patch('crm.services.Note.objects.create', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                convert_lead(self.lead, self.owner)

        self.assertEqual(Contact.objects.count(), contact_count)
        self.assertEqual(Company.objects.count(), company_count)
        self.assertEqual(Deal.objects.count(), deal_count)
        self.assertEqual(Note.objects.count(), note_count)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'qualified')
        self.assertIsNone(self.lead.converted_at)
        self.assertIsNone(self.lead.converted_contact_id)

    def test_deleting_converted_lead_keeps_converted_contact(self):
        result = convert_lead(self.lead, self.owner, create_deal=False)
        contact_id = result.contact.pk
        self.client.force_login(self.owner)
        response = self.client.post(reverse('lead_delete', args=[self.lead.pk]))
        self.assertRedirects(response, reverse('lead_list'))
        self.assertTrue(Contact.objects.filter(pk=contact_id).exists())

    def test_converted_lead_cannot_be_edited(self):
        convert_lead(self.lead, self.owner, create_deal=False)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('lead_edit', args=[self.lead.pk]))
        self.assertRedirects(response, reverse('lead_detail', args=[self.lead.pk]))

    def test_dashboard_lead_metrics_are_owner_scoped_and_aggregated(self):
        Lead.objects.create(owner=self.owner, name='New Owner Lead', status='new')
        Lead.objects.create(owner=self.owner, name='Qualified Owner Lead', status='qualified')
        convert_lead(self.lead, self.owner, create_deal=False)
        Lead.objects.create(owner=self.other_user, name='Private New', status='new')
        Lead.objects.create(owner=self.other_user, name='Private Converted', status='converted')

        self.client.force_login(self.owner)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['new_leads'], 1)
        self.assertEqual(response.context['qualified_leads'], 1)
        self.assertEqual(response.context['conversion_rate'], Decimal('33.33'))
        self.assertContains(response, 'Leads')

    def test_converted_contact_shows_source_and_source_lead_link(self):
        result = convert_lead(self.lead, self.owner, create_deal=False)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('contact_detail', args=[result.contact.pk]))
        self.assertContains(response, 'Referral')
        self.assertContains(response, 'Converted from lead')

        response = self.client.get(reverse('contact_list'), {'source': 'referral'})
        self.assertContains(response, result.contact.name)


class Contact360TimelineTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='timeline-owner', password='StrongPassword!248')
        self.other = User.objects.create_user(username='timeline-other', password='StrongPassword!248')
        self.contact = Contact.objects.create(owner=self.owner, name='Timeline Contact', email='timeline@example.com')
        self.deal = Deal.objects.create(contact=self.contact, title='Timeline Deal', stage='new', value=100, currency='PKR')
        self.note = Note.objects.create(owner=self.owner, contact=self.contact, deal=self.deal, content='Timeline note')
        self.task = Task.objects.create(owner=self.owner, contact=self.contact, deal=self.deal, title='Timeline task', due_date=timezone.now() + timedelta(days=1), status='completed', completed_at=timezone.now())

    def test_timeline_contains_all_event_types_and_is_newest_first(self):
        lead = Lead.objects.create(owner=self.owner, name='Timeline lead', status='converted', converted_contact=self.contact, converted_at=timezone.now() - timedelta(minutes=1))
        change = DealStageHistory.objects.create(deal=self.deal, from_stage='new', to_stage='negotiation', changed_by=self.owner)
        timeline = get_contact_timeline(self.contact, self.owner, limit=50)['events']
        types = [event['type'] for event in timeline]
        self.assertIn('system', types)
        self.assertIn('lead_converted', types)
        self.assertIn('note', types)
        self.assertIn('task_created', types)
        self.assertIn('task_completed', types)
        self.assertIn('deal_created', types)
        self.assertIn('stage_change', types)
        self.assertEqual([event['timestamp'] for event in timeline], sorted((event['timestamp'] for event in timeline), reverse=True))
        self.assertEqual(timeline[0]['related']['history'].pk, change.pk)

    def test_timeline_filters_invalid_params_and_load_more_cursor(self):
        now = timezone.now()
        for day in range(3):
            Note.objects.create(owner=self.owner, contact=self.contact, content=f'Note {day}', created_at=now - timedelta(days=day))
        first = get_contact_timeline(self.contact, self.owner, event_types=['notes'], limit=2)
        second = get_contact_timeline(self.contact, self.owner, event_types=['notes'], limit=2, before=first['next_cursor'])
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['events']), 2)
        self.assertEqual(len({event['related']['note'].pk for event in first['events']} & {event['related']['note'].pk for event in second['events']}), 0)
        invalid = get_contact_timeline(self.contact, self.owner, event_types=['wrong'], limit='999', before='not-a-date')
        self.assertLessEqual(len(invalid['events']), 50)

    def test_summary_numbers_are_grouped_by_currency(self):
        Deal.objects.create(contact=self.contact, title='Open USD', stage='negotiation', value=125, currency='USD')
        Deal.objects.create(contact=self.contact, title='Won USD', stage='won', value=75, currency='USD')
        summary = get_contact_summary(self.contact, self.owner)
        self.assertEqual(summary['total_deals'], 3)
        self.assertEqual(summary['notes_count'], 1)
        self.assertEqual(summary['pending_tasks'], 0)
        self.assertEqual(summary['overdue_tasks'], 0)
        self.assertIn({'currency': 'USD', 'value': Decimal('125')}, summary['open_pipeline'])
        self.assertIn({'currency': 'USD', 'value': Decimal('75')}, summary['won_revenue'])
        self.assertIsNotNone(summary['last_activity'])

    def test_page_and_endpoint_are_owner_isolated_and_login_required(self):
        url = reverse('contact_detail', args=[self.contact.pk])
        endpoint = reverse('contact_timeline', args=[self.contact.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), 'Timeline')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(endpoint).status_code, 404)

    def test_dashboard_recent_activity_is_owner_only(self):
        other_contact = Contact.objects.create(owner=self.other, name='Private timeline', email='private@example.com')
        Note.objects.create(owner=self.other, contact=other_contact, content='Private note')
        self.client.force_login(self.owner)
        response = self.client.get(reverse('dashboard'))
        names = [event['title'] for event in get_recent_contact_activity(self.owner)['events']]
        self.assertIn('Timeline task', ' '.join(names))
        self.assertNotIn('Private note', ' '.join(names))
        self.assertContains(response, 'Recent activity')

    def test_contact_timeline_query_count_is_fixed(self):
        for index in range(25):
            Note.objects.create(owner=self.owner, contact=self.contact, content=f'Query note {index}')
        self.client.force_login(self.owner)
        with self.assertNumQueries(20):
            response = self.client.get(reverse('contact_detail', args=[self.contact.pk]))
        self.assertEqual(response.status_code, 200)
