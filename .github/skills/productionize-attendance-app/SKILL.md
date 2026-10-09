---
name: productionize-attendance-app
description: Maintain the Flask attendance system for real institutional use, including safe account bootstrap, explicit database lifecycle operations, administrator workflows, security, and regression tests.
---

# Productionize the Attendance App

Use this skill when changing the College Attendance Prediction System for real-use data or operating its local SQLite installation.

## Operational safety

- Never seed demo users, profiles, subjects, or attendance during app import or startup.
- Treat `database/attendance.db` as user data. Inspect its exact resolved path, integrity, and contents before any proposed reset or delete. Never reset it implicitly.
- Destructive reset must be an explicit operator action, restricted to a file-backed local SQLite database, require an exact path confirmation, and create a restorable timestamped backup first.
- Bootstrap the first administrator only from private environment variables. Do not put credentials in templates, tracked source, test fixtures using production values, logs, or documentation. Never print a password.
- Keep `.env`, local database files, and database backups ignored by Git. Document variable names and placeholders only.

## Access and data management

- Protect every administrator mutation with authentication and an explicit `admin` role check; keep CSRF protection enabled for browser forms.
- Hash newly set passwords with Argon2id; verify legacy Werkzeug hashes and transparently rehash them after successful login. Never serialize password hashes or plaintext passwords to templates, APIs, or audit events.
- Keep CSRF enabled outside test mode, apply bounded request limits, use HMAC-obscured account/IP identifiers, secure session-cookie defaults, security headers, and safe error responses. Never trust forwarded headers unless an exact trusted-proxy hop count is configured.
- Record successful/failed login and sensitive account, academic, attendance, correction, and leave activity in append-only security events with redacted, allowlisted fields. Restrict the Security Center to Super Admins and derive check statuses from live configuration; do not present a fabricated score or compliance certification.
- Validate role choices, unique usernames/profile identifiers, foreign keys, attendance statuses, dates, and the student/subject/day uniqueness rule before committing.
- Guard against deleting or demoting the current/last administrator.
- When removing accounts or profiles, handle dependent attendance and subject assignments intentionally; avoid dangling foreign-key references and explain the resulting deletion behavior in the UI.
- Cover create, read, update, and delete paths for student/faculty accounts and profiles, subjects and faculty assignments, and attendance records.

## Change workflow

1. Inspect the app factory, configuration, models, route authorization, templates, tests, `.gitignore`, and the existing database without changing it.
2. Make small changes that retain existing routes and behavior unless a security or production-data requirement requires otherwise.
3. Add regression tests for empty startup, bootstrap/reset guards, admin authorization, Argon2id/legacy password handling, throttling, headers/CSRF, audit redaction and authorization, uniqueness/validation, dependent records, and each CRUD workflow touched.
4. Run `pytest`, inspect diagnostics, and search tracked source/docs/templates for credentials and stale demo labels before reporting.
5. Report whether any existing database was changed. If reset is still required, identify its exact path and wait for explicit operator authorization; never imply startup performed it.
