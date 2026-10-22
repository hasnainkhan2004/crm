# CRM

A customer relationship management (CRM) web app built with Django. It helps you keep track of contacts, sales deals, and activity notes in one place.

## Features

- Sign up with a username, email address, and password.
- Sign in with either your username or email address.
- Manage contacts and their associated deals and activity notes.
- Track deal stages: New, Contacted, Negotiation, Won, and Lost.
- View a dashboard with contact and deal totals, deal value, and a chart of deal stages.
- Keep CRM records private to the account that owns them.
- Use Django Admin to manage application data.

## Run locally

Open a terminal in the project directory (the directory containing `manage.py`).

### Windows PowerShell

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install "Django>=6.0,<6.1"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

If PowerShell does not recognize `py`, use `python` in its place. If script activation is disabled, run the venv Python directly instead:

```powershell
.\venv\Scripts\python.exe -m pip install "Django>=6.0,<6.1"
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py createsuperuser
.\venv\Scripts\python.exe manage.py runserver
```

## Project layout

```text
crm/              CRM models, forms, views, tests, migrations, and templates
crmproject/       Django project settings and URL configuration
manage.py         Django management commands
db.sqlite3        Local SQLite database (created locally; not committed)
```

