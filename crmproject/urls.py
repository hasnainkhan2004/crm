from django.contrib import admin
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from crm.forms import (
    EmailOrUsernameAuthenticationForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from crm import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.dashboard, name='dashboard'),
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='crm/login.html',
            authentication_form=EmailOrUsernameAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('signup/', views.signup, name='signup'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='crm/password_reset_form.html',
            email_template_name='crm/password_reset_email.txt',
            subject_template_name='crm/password_reset_subject.txt',
            form_class=StyledPasswordResetForm,
            success_url=reverse_lazy('password_reset_done'),
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='crm/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='crm/password_reset_confirm.html',
            form_class=StyledSetPasswordForm,
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='crm/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),

    path('leads/', views.LeadListView.as_view(), name='lead_list'),
    path('leads/add/', views.LeadCreateView.as_view(), name='lead_add'),
    path('leads/<int:pk>/', views.LeadDetailView.as_view(), name='lead_detail'),
    path('leads/<int:pk>/edit/', views.LeadUpdateView.as_view(), name='lead_edit'),
    path('leads/<int:pk>/delete/', views.LeadDeleteView.as_view(), name='lead_delete'),
    path('leads/<int:pk>/status/', views.lead_status_change, name='lead_status'),
    path('leads/<int:pk>/convert/', views.LeadConvertView.as_view(), name='lead_convert'),

    path('contacts/', views.ContactListView.as_view(), name='contact_list'),
    path('contacts/add/', views.ContactCreateView.as_view(), name='contact_add'),
    path('contacts/<int:pk>/', views.ContactDetailView.as_view(), name='contact_detail'),
    path('contacts/<int:pk>/edit/', views.ContactUpdateView.as_view(), name='contact_edit'),
    path('contacts/<int:pk>/delete/', views.ContactDeleteView.as_view(), name='contact_delete'),
    path('contacts/<int:pk>/notes/add/', views.contact_note_add, name='contact_note_add'),
    path('contacts/<int:pk>/timeline/', views.contact_timeline, name='contact_timeline'),

    path('companies/', views.CompanyListView.as_view(), name='company_list'),
    path('companies/add/', views.CompanyCreateView.as_view(), name='company_add'),
    path('companies/quick-create/', views.company_quick_create, name='company_quick_create'),
    path('companies/<int:pk>/', views.CompanyDetailView.as_view(), name='company_detail'),
    path('companies/<int:pk>/edit/', views.CompanyUpdateView.as_view(), name='company_edit'),
    path('companies/<int:pk>/delete/', views.CompanyDeleteView.as_view(), name='company_delete'),

    path('deals/', views.DealListView.as_view(), name='deal_list'),
    path('deals/board/', views.deal_board, name='deal_board'),
    path('deals/add/', views.DealCreateView.as_view(), name='deal_add'),
    path('deals/<int:pk>/move/', views.move_deal_view, name='deal_move'),
    path('deals/<int:pk>/', views.DealDetailView.as_view(), name='deal_detail'),
    path('deals/<int:pk>/edit/', views.DealUpdateView.as_view(), name='deal_edit'),
    path('deals/<int:pk>/delete/', views.DealDeleteView.as_view(), name='deal_delete'),
    path('deals/<int:pk>/notes/add/', views.deal_note_add, name='deal_note_add'),
    path('notes/<int:pk>/edit/', views.NoteUpdateView.as_view(), name='note_edit'),
    path('notes/<int:pk>/delete/', views.NoteDeleteView.as_view(), name='note_delete'),

    path('tasks/', views.TaskListView.as_view(), name='task_list'),
    path('tasks/add/', views.TaskCreateView.as_view(), name='task_add'),
    path('tasks/<int:pk>/edit/', views.TaskUpdateView.as_view(), name='task_edit'),
    path('tasks/<int:pk>/delete/', views.TaskDeleteView.as_view(), name='task_delete'),
    path('tasks/<int:pk>/toggle-complete/', views.toggle_task_complete, name='task_toggle_complete'),
]