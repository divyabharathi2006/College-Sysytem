# College Attendance Prediction System

A Flask-based attendance management and prediction system for institutional use. Production startup never creates, drops, or resets tables; initialize and upgrade the schema explicitly with Flask-Migrate.

## Features
- Role-based admin, faculty, and student access
- Administrator management of user accounts, student/faculty profiles, subjects, assignments, and attendance
- Attendance CSV import/export
- Session-linked attendance with legacy daily-row compatibility
- Student leave requests and department-scoped attendance correction review
- Dashboard analytics and attendance predictions
- SQLite storage with a path configurable through `DATABASE_URL`
- Additive Alembic migrations and normalized academic hierarchy foundations
- Argon2id password hashing with transparent migration of legacy Werkzeug hashes
- Enabled/disabled accounts, last-login tracking, persistent revocable sessions, and Super Admin account/session controls
- Single-use expiring password-reset tokens stored only as keyed hashes; optional SMTP delivery
- Request throttling, hardened session cookies, security headers, append-only security events, and an administrator Security Center
- Database-backed Super Admin operational controls and owner-bound, expiring attendance-import previews
- Separate university-student, affiliated-student, and affiliated-college SQLite registries
- Verified student email activation, generated DBU registration numbers, administrator identification checks, and college welfare records

## Homepage gallery photos

The homepage gallery loads three illustrative stock photos directly from the third-party host `images.pexels.com`; no image binaries are stored in this repository. Each caption identifies the photographer and links to the photographer’s Pexels profile and the original photo page. The images do not depict Divyabharathi University, its campus, or its events, and the people shown do not endorse the university. Pexels photos are free to use on websites; attribution is appreciated, and the [Pexels license](https://www.pexels.com/license/) prohibits implying endorsement or using photos misleadingly.

- Campus students — [RDNE Stock project](https://www.pexels.com/@rdne/), [photo source](https://www.pexels.com/photo/college-students-in-a-university-campus-7683694/)
- Library collaboration — [Yan Krukau](https://www.pexels.com/@yankrukov/), [photo source](https://www.pexels.com/photo/students-studying-inside-the-library-8199659/)
- Graduation celebration — [clmcdk fejcn](https://www.pexels.com/@clmcdk-fejcn-2057437867/), [photo source](https://www.pexels.com/photo/graduates-celebrating-with-caps-in-the-air-29229903/)

## Requirements
- Python 3.11+
- Dependencies in `requirements.txt`

## First-time setup

1. Create and activate a virtual environment, then install dependencies:

	```powershell
	python -m venv .venv
	.\.venv\Scripts\Activate.ps1
	pip install -r requirements.txt
	```

2. Edit the ignored local `.env` file. Set a long, unique random `SECRET_KEY` and the private `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` values. Do not share or commit this file. The application never prints or displays the bootstrap password.

3. Apply the schema migration before starting the application. For an existing installation, back up the database first. The adoption revisions leave existing records in place; the attendance workflow revision adds nullable session links and request tables while retaining historical daily attendance and status values. Revision `20261009_10` adds an optional non-unique student roll number, a keyed DOB verifier, and the forced-password-change account flag:

	```powershell
	flask --app app db upgrade
	```

	For a new installation, this creates the current schema. Do not use `db.create_all()` as a production schema-management operation; create future schema changes as Alembic revisions and apply them with `flask db upgrade`.

4. Initialize the three separate registry databases explicitly:

	```powershell
	flask --app app init-registry-databases
	```

	This additive command creates missing tables only in `database/university_students.db`, `database/affiliated_students.db`, and `database/affiliated_colleges.db` by default. Super Admins can also start the same additive operation using **Initialize registries** on the Affiliated colleges or Student management page when registry storage is not ready; that action is explicit and protected by login, role authorization, and CSRF. It refuses in-memory or overlapping database paths and never drops registry tables or attendance data. The three `*_DATABASE_URL` variables in `.env` can point to other file-backed SQLite databases. Keep all database files and backups private; `.gitignore` excludes them.

5. Inspect `database/attendance.db` before replacing any existing data. To start with a clean database, run the explicit reset only after confirming the displayed path is the intended database. The command makes a timestamped backup beside the database before recreating its schema. It resets only the attendance database; the three student/college registry files are left untouched:

	```powershell
	$db = (Join-Path (Get-Location) 'database\attendance.db')
	flask --app app reset-database --confirm-path $db
	```

	Reset is never performed on application startup. It is restricted to a file-backed SQLite database and requires the exact configured database path. Keep the backup until the new installation is verified; after a reset, run `flask --app app db upgrade` to initialize migration tracking.

6. Create the first administrator once:

	```powershell
	flask --app app bootstrap-admin
	```

	Bootstrap reads only the two `BOOTSTRAP_ADMIN_*` variables from the process environment or ignored local `.env`. It refuses to run when an administrator already exists.

7. Start the app with the single project entry point and open <http://127.0.0.1:5000>:

	```powershell
	python app.py
	```

If an existing database is present, startup leaves its records unchanged. Use the reset command only when you have inspected the database and intentionally want an empty attendance installation. Faculty accounts continue to be managed from the administrator dashboard.

## Student and affiliated-college registries

University registration and oversight remain managed by Super Admins from **Student management**. The Admin Dashboard's **Create user accounts** actions link directly to student registration and affiliated-college login management. Super Admins can create named, college-scoped staff logins from **Affiliated colleges**, choose their role, update their name or role, and disable accounts. Every affiliated staff role sees only its assigned college's student roster. **College Admins** can register and edit that college's student registration details; **Registrars** can register students; **Attendance Officers** have read-only access to the college's attendance summary. Only Super Admins can create, disable, or assign affiliated-college staff accounts. College accounts must change their temporary password at first sign-in. Disabling a staff account revokes its active sessions, and deactivating a college disables all of its staff accounts. Existing legacy affiliated-college accounts retain their former view-and-register permissions.

A university student's registration number is assigned sequentially from the next available number under the `DBU300` prefix (for example, `DBU3001`, `DBU3002`). Each affiliated college receives a stable, separately assigned prefix starting at `DBU301`; its sequence is independent (for example, `DBU3011`, `DBU3012`). The form displays a preview, and the server determines the next number again on submission. Roll numbers are stored separately and may repeat between colleges; registration numbers, email addresses, and normalized phone numbers may not be reused. Existing attendance profiles remain in `attendance.db`; registration details are stored in the corresponding student registry and linked by the account ID.

Sessions expire after 30 minutes without activity by default and are extended while the user is active. Set `SESSION_IDLE_TIMEOUT_MINUTES` to another positive number to change this inactivity limit. Authenticated pages are sent with no-store headers so returning to a protected page after logout requires signing in again.

New student accounts remain disabled until the student confirms their email using the expiring, single-use verification link. Configure `SMTP_HOST` and `SMTP_FROM` (and the optional SMTP credentials) before creating registrations. The student must change the administrator-set temporary password after signing in. The registry stores the identification type and whether an administrator checked it; it does not store ID numbers or document images. The checked flag records an administrator's manual verification and does not claim to validate a government ID against an issuing authority.

The student registration form collects the generated registration-number preview, college roll number, full name, login username and temporary password, email, phone, gender, date of birth, parents' names, community, department/course, year, section, semester, address, identification type and manual check status, and optional accessibility support information. Email and phone are validated and unique across student registrations; student accounts remain disabled until email verification. The form never requests ID numbers or identity-document uploads.

The affiliated-college registry stores college code, address, contact details, affiliation, staff details, student strength, an administrator-maintained attendance-summary description, infrastructure, courses, anti-ragging committee status, counseling services, scholarships, and extracurricular activities. Separately, the Attendance Officer portal calculates read-only totals and present percentages from actual attendance records belonging to students in that officer's assigned college; it does not expose other colleges' attendance or student contact details. Deactivating a college retains its record and registration prefix so historical student records remain interpretable; new registrations are blocked and linked college staff accounts are disabled.

Registry files are separate SQLite databases and do not support cross-file foreign keys or a distributed transaction. The app creates the core attendance profile and registry detail in one registration workflow and uses the unique contact reservation in `attendance.db` to prevent duplicate registrations across the two student registries. Keep the files on reliable local storage and include all four databases in the institution's protected backup and restore process.

## Explicit student roster import and first sign-in

The student roster importer is never run at startup. After taking an operator-managed database backup and applying the schema migration, first run a dry-run from the project root:

```powershell
flask --app app import-students
```

The default input is `tamil_nadu_student_database_6000.csv` in the project root; an alternate CSV path may be passed as the command argument. Dry-run is the default and writes no student or account records. It validates the required columns, required profile values, strict `DD-MM-YYYY` birth dates, duplicate student IDs, and conflicts with existing usernames/register numbers. Diagnostics contain row numbers and safe error codes only, not row values. Student ID becomes both the login username and register number; roll number is intentionally non-unique, and attendance summary percentages are not converted into attendance events.

Only after reviewing a successful dry-run, explicitly commit the import:

```powershell
flask --app app import-students --commit
```

Any validation or identifier conflict prevents the entire import; a database uniqueness conflict also rolls back the whole transaction. Imported inactive accounts are disabled. Only the mapped profile fields are stored. DOB is never stored or rendered; first-login verification uses a purpose-separated keyed HMAC verifier. Do not commit the source CSV or run this command unless the operator has approved the import and confirmed the target database.

Imported students choose **First sign-in for imported students** on the login page and enter their student ID plus DOB in `DD-MM-YYYY` format. After verification, the app immediately requires a strong password (12+ characters, at least three character types), replaces the DOB verifier with `NULL`, and rotates the validated server-side session. Normal existing username/password accounts—including staff and pre-existing students—continue using their current sign-in flow.

## CSV attendance format and staged import

```csv
register_number,subject_code,date,status
22CS001,CS201,2026-10-01,PRESENT
22CS002,CS201,2026-10-01,ABSENT
```

New status values `PRESENT`, `ABSENT`, `LATE`, and `EXCUSED` are supported; legacy `OD` and `LEAVE` values remain unchanged and supported for compatibility. Attendance may be a legacy daily row or linked to a scheduled class session. A leave request approval changes only the request status and does not create attendance rows.

The only supported attendance CSV columns are `register_number`, `subject_code`, `date`, and `status`. Uploads are UTF-8 CSV files, limited by `MAX_ATTENDANCE_IMPORT_BYTES` (2 MiB by default) and `MAX_ATTENDANCE_IMPORT_ROWS` (2,000 by default). Uploading validates every row and creates a database-backed preview bound to the initiating Super Admin; it does not write attendance. The preview reports accepted rows and validation/duplicate errors. Accepted rows are committed together only after an explicit confirmation phrase, are rechecked for conflicts, and roll back together if commit fails. Previews expire after `ATTENDANCE_IMPORT_STAGE_TTL_MINUTES` (15 by default); the owner can cancel a preview. API uploads return `202` with preview and commit URLs, and API commits must include `confirmation_phrase` set to `COMMIT ATTENDANCE IMPORT <stage_id>`.

Faculty can edit attendance within the configured correction window. Edits after that window create a pending correction request instead; a different authorized reviewer must approve it before the recorded status changes. Configure `ATTENDANCE_CORRECTION_WINDOW_HOURS` in the local environment (`24` by default). Correction records persist previous/new statuses, reason, requester/reviewer identities and roles, and request/review timestamps. Sensitive account, academic, attendance, correction, and leave mutations are also captured in the Security Center; ordinary read-only page views are not audited.

## Prediction methodology

The system computes attendance from stored records and applies deterministic formulas for safe absences, required classes, and future predictions. The optional ML module is used only when enough historical data exists; otherwise the deterministic engine is used.

## Security configuration and operations

- New passwords use Argon2id. Existing Werkzeug password hashes are verified at login and upgraded after successful authentication; credentials and password hashes are never included in audit events.
- Flask-Limiter uses `memory://` by default for local, single-process use. Configure `RATELIMIT_STORAGE_URI` to a shared Redis-compatible store for multi-worker deployments. Login throttling keys HMAC the normalized account and direct client address; other limits HMAC the address.
- `SECURITY_HMAC_KEY` should be a unique random secret. If omitted, `SECRET_KEY` is used. `SESSION_COOKIE_SECURE=true` should be set when served over HTTPS; `SESSION_LIFETIME_HOURS` defaults to 8. Cookies are HttpOnly and SameSite=Lax, and Flask-Login session protection is enabled.
- `TRUSTED_PROXY_HOPS` defaults to `0`; forwarded client/protocol headers are ignored. Set it only to the exact number of trusted proxies in front of the app. HSTS is emitted only when Flask considers the request secure; configure the proxy explicitly if TLS terminates before Flask.
- CSRF protection remains enabled in normal application mode. Tests intentionally disable it in their testing configuration.
- Logout is POST-only and protected by CSRF. Every authenticated cookie session must also match an unexpired, unrevoked server-side session row; disabling an account, resetting its password, or using an administrator revoke action invalidates those rows.
- Super Admins manage enable/disable state and active-session revocation at `/admin/accounts/security`. The current administrator cannot disable their own account, and the last enabled Super Admin cannot be disabled, demoted, or deleted.
- Password reset requests use a generic response and are rate-limited to avoid account enumeration. Reset tokens are random, single-use, expire after `PASSWORD_RESET_LIFETIME_MINUTES` (30 by default), and are persisted only as HMAC digests. New/reset passwords require at least 12 characters and three character classes.
- Reset email is sent only when `SMTP_HOST` and `SMTP_FROM` are configured and the account has a student/faculty profile email. Set `SMTP_PORT`, optional `SMTP_USERNAME`/`SMTP_PASSWORD`, `SMTP_USE_TLS`, and preferably `PUBLIC_BASE_URL` as needed. Without SMTP configuration, requests remain generic but no email is sent; password-reset email delivery is therefore unavailable.
- The CSP allows `unsafe-inline` for scripts and styles because current templates contain inline chart code and styling; this is a documented compatibility limitation, not a nonce-based policy. Moving inline code to static assets/nonces is deferred.
- Open `/admin/security` as a Super Admin to review live configuration checks and recent audit events. Checks report actual configuration and do not produce a security score or compliance certification.
- The Security Center also exposes persisted maintenance, read-only, attendance modification, import, and export switches. Changes require the exact displayed phrase. While maintenance is active, login and the Super Admin Security Center/control routes remain available; other application requests are rejected server-side. Read-only mode rejects unsafe HTTP methods except for operational controls, logout, and staged-import cancellation. Attendance/import/export switches are enforced before route handlers run.
- Force-revoking all sessions and disabling an account require exact confirmation phrases. Disabling an account also revokes its sessions; a Super Admin cannot disable their own account or the last enabled Super Admin.
- Security events retain HMAC IP identifiers and bounded user-agent strings, and store only allowlisted business fields. Rows are append-only at the ORM layer and, for SQLite, protected by database triggers. Back up the database before applying the additive security-events revision with the normal `flask --app app db upgrade` operation.

MFA, configurable lockout policy beyond request throttling, and CSP nonces remain deferred; this application does not claim to implement them.

The additive account/session/reset schema is revision `20261008_07`, after `20261008_06`. Operational controls and staged import records are added by revision `20261008_08`. This code change does not apply migrations or inspect/modify the configured live database. Before enabling this release against an existing installation, take and verify an operator-managed backup, then apply the pending Alembic revisions in a planned maintenance/deployment step; do not reset or recreate the database.

## Testing

```powershell
pytest
```
