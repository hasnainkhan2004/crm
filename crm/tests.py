from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Contact, Deal


User = get_user_model()


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
