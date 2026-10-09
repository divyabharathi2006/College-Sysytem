import sqlite3
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from app import create_app, db
from models.academic import AcademicYear, Batch, ClassSession, Classroom, Course, Department, Section, Semester
from models.attendance import Attendance
from models.student import Student
from models.subject import Subject
from models.user import User
from permissions import Role, can_access, has_role, normalize_role


def _create_legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        '''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, username VARCHAR(80) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE faculty (
            id INTEGER PRIMARY KEY, employee_id VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(120) NOT NULL, email VARCHAR(120) NOT NULL,
            department VARCHAR(80) NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id)
        );
        CREATE TABLE students (
            id INTEGER PRIMARY KEY, register_number VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(120) NOT NULL, email VARCHAR(120) NOT NULL, phone VARCHAR(30) NOT NULL,
            department VARCHAR(80) NOT NULL, year VARCHAR(20) NOT NULL, section VARCHAR(20) NOT NULL,
            semester VARCHAR(20) NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id)
        );
        CREATE TABLE subjects (
            id INTEGER PRIMARY KEY, subject_code VARCHAR(30) NOT NULL UNIQUE,
            subject_name VARCHAR(120) NOT NULL, department VARCHAR(80) NOT NULL,
            semester VARCHAR(20) NOT NULL, faculty_id INTEGER REFERENCES faculty(id)
        );
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
            subject_id INTEGER NOT NULL REFERENCES subjects(id), date DATE NOT NULL,
            status VARCHAR(20) NOT NULL, marked_by INTEGER REFERENCES users(id),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT unique_student_subject_day UNIQUE (student_id, subject_id, date)
        );
        '''
    )
    password_hash = generate_password_hash('migration-test-password')
    connection.execute(
        'INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)',
        (17, 'legacy-admin', password_hash, 'admin'),
    )
    connection.execute(
        'INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)',
        (29, 'legacy-student', generate_password_hash('student-password'), 'student'),
    )
    connection.execute(
        'INSERT INTO students (id, register_number, name, email, phone, department, year, section, semester, user_id) '
        'VALUES (39, ?, ?, ?, ?, ?, ?, ?, ?, 29)',
        ('22CS039', 'Legacy Student', 'legacy@example.test', '555-0039', 'CSE', '2', 'A', '4'),
    )
    connection.execute(
        'INSERT INTO subjects (id, subject_code, subject_name, department, semester) VALUES (48, ?, ?, ?, ?)',
        ('CS204', 'Legacy Systems', 'CSE', '4'),
    )
    connection.execute(
        'INSERT INTO attendance (id, student_id, subject_id, date, status, marked_by) VALUES (61, 39, 48, ?, ?, 17)',
        ('2026-10-01', 'PRESENT'),
    )
    connection.execute(
        'INSERT INTO attendance (id, student_id, subject_id, date, status, marked_by) VALUES (62, 39, 48, ?, ?, 17)',
        ('2026-10-02', 'OD'),
    )
    connection.execute(
        'INSERT INTO attendance (id, student_id, subject_id, date, status, marked_by) VALUES (63, 39, 48, ?, ?, 17)',
        ('2026-10-03', 'LEAVE'),
    )
    connection.commit()
    connection.close()
    return password_hash


def test_production_app_requires_migrations_instead_of_creating_tables(tmp_path, monkeypatch):
    database_path = tmp_path / 'not-created-until-migrated.sqlite'
    monkeypatch.setenv('SECRET_KEY', 'test-foundation-secret')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path.as_posix()}')
    app = create_app()

    with app.app_context():
        assert inspect(db.engine).get_table_names() == []
    result = app.test_cli_runner().invoke(args=['db', 'upgrade'])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert {
            'users', 'students', 'departments', 'class_sessions', 'prediction_history',
            'notifications', 'notification_preferences', 'user_sessions', 'password_reset_tokens',
            'announcements',
        }.issubset(set(inspect(db.engine).get_table_names()))


def test_registry_database_initialization_is_explicit_additive_and_separate(tmp_path, monkeypatch):
    paths = {
        'DATABASE_URL': tmp_path / 'attendance.sqlite',
        'UNIVERSITY_STUDENTS_DATABASE_URL': tmp_path / 'university.sqlite',
        'AFFILIATED_STUDENTS_DATABASE_URL': tmp_path / 'affiliated.sqlite',
        'AFFILIATED_COLLEGES_DATABASE_URL': tmp_path / 'colleges.sqlite',
    }
    monkeypatch.setenv('SECRET_KEY', 'test-registry-init-secret')
    for key, path in paths.items():
        monkeypatch.setenv(key, f'sqlite:///{path.as_posix()}')
    app = create_app()
    assert not paths['DATABASE_URL'].exists()
    assert not paths['UNIVERSITY_STUDENTS_DATABASE_URL'].exists()
    assert not paths['AFFILIATED_STUDENTS_DATABASE_URL'].exists()
    assert not paths['AFFILIATED_COLLEGES_DATABASE_URL'].exists()

    runner = app.test_cli_runner()
    initialized = runner.invoke(args=['init-registry-databases'])
    assert initialized.exit_code == 0, initialized.output
    repeated = runner.invoke(args=['init-registry-databases'])
    assert repeated.exit_code == 0, repeated.output
    assert not paths['DATABASE_URL'].exists()
    assert paths['UNIVERSITY_STUDENTS_DATABASE_URL'].exists()
    assert paths['AFFILIATED_STUDENTS_DATABASE_URL'].exists()
    assert paths['AFFILIATED_COLLEGES_DATABASE_URL'].exists()
    with app.app_context():
        assert {'university_student_records'} == set(
            inspect(db.engines['university_students']).get_table_names()
        )
        assert {'affiliated_student_records'} == set(
            inspect(db.engines['affiliated_students']).get_table_names()
        )
        assert {'affiliated_college_records'} == set(
            inspect(db.engines['affiliated_colleges']).get_table_names()
        )
    reset = runner.invoke(args=[
        'reset-database', '--confirm-path', str(paths['DATABASE_URL']),
    ])
    assert reset.exit_code == 0, reset.output
    with app.app_context():
        assert {'university_student_records'} == set(
            inspect(db.engines['university_students']).get_table_names()
        )
        assert {'affiliated_student_records'} == set(
            inspect(db.engines['affiliated_students']).get_table_names()
        )
        assert {'affiliated_college_records'} == set(
            inspect(db.engines['affiliated_colleges']).get_table_names()
        )


def test_flask_migrate_adopts_legacy_schema_and_preserves_identity_and_records(tmp_path, monkeypatch):
    database_path = tmp_path / 'legacy-attendance.sqlite'
    expected_hash = _create_legacy_database(database_path)
    monkeypatch.setenv('SECRET_KEY', 'test-migration-secret')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path.as_posix()}')
    app = create_app()
    result = app.test_cli_runner().invoke(args=['db', 'upgrade'], catch_exceptions=False)
    assert result.exit_code == 0, f'{result.output}\n{result.stderr}\n{result.exception!r}\n{result.exc_info!r}'

    with app.app_context():
        administrator = db.session.get(User, 17)
        assert administrator.username == 'legacy-admin'
        assert administrator.role == Role.SUPER_ADMIN.value
        assert administrator.password_hash == expected_hash
        assert has_role(administrator, Role.SUPER_ADMIN)
        assert db.session.get(User, 29).role == Role.STUDENT.value
        student = db.session.get(Student, 39)
        assert student.department == 'CSE'
        assert student.department_id is None
        assert Attendance.query.count() == 3
        assert db.session.get(Attendance, 61).status == 'PRESENT'
        assert db.session.get(Attendance, 62).status == 'OD'
        assert db.session.get(Attendance, 63).status == 'LEAVE'
        assert {'departments', 'courses', 'academic_years', 'semesters', 'batches', 'sections', 'classrooms', 'class_sessions'}.issubset(
            set(inspect(db.engine).get_table_names())
        )
        assert {'prediction_history', 'notifications', 'notification_preferences'}.issubset(
            set(inspect(db.engine).get_table_names())
        )
        assert 'security_events' in inspect(db.engine).get_table_names()
        assert 'affiliated_college_code' in {
            column['name'] for column in inspect(db.engine).get_columns('users')
        }
        assert 'display_name' in {
            column['name'] for column in inspect(db.engine).get_columns('users')
        }
        db.session.execute(text(
            "INSERT INTO security_events "
            "(actor_role, action, resource_type, created_at, status, reason, ip_hash, user_agent) "
            "VALUES ('admin', 'migration.test', 'test', CURRENT_TIMESTAMP, 'success', 'test', :hash, '')"
        ), {'hash': 'a' * 64})
        db.session.commit()
        with pytest.raises(Exception):
            db.session.execute(text('DELETE FROM security_events'))
        db.session.rollback()
        assert 'department_id' in {column['name'] for column in inspect(db.engine).get_columns('students')}
        assert {'departments'} == {
            foreign_key['referred_table']
            for foreign_key in inspect(db.engine).get_foreign_keys('students')
            if foreign_key['constrained_columns'] == ['department_id']
        }
        assert {'departments'} == {
            foreign_key['referred_table']
            for foreign_key in inspect(db.engine).get_foreign_keys('users')
            if foreign_key['constrained_columns'] == ['department_id']
        }

        department = Department(code='CSE', name='Computer Science')
        year = AcademicYear(label='2026-2027', starts_on=date(2026, 6, 1), ends_on=date(2027, 5, 31))
        db.session.add_all([department, year])
        db.session.flush()
        semester = Semester(
            academic_year_id=year.id, number=1,
            starts_on=date(2026, 6, 1), ends_on=date(2026, 12, 31),
        )
        course = Course(department_id=department.id, code='CS', name='Computer Science')
        db.session.add_all([semester, course])
        db.session.flush()
        batch = Batch(course_id=course.id, academic_year_id=year.id, code='2026')
        db.session.add(batch)
        db.session.flush()
        section = Section(batch_id=batch.id, code='A')
        db.session.add(section)
        db.session.flush()
        first_session = ClassSession(
            section_id=section.id, semester_id=semester.id, subject_id=48,
            weekday=3, starts_at=time(9, 0), ends_at=time(10, 0),
        )
        second_session = ClassSession(
            section_id=section.id, semester_id=semester.id, subject_id=48,
            weekday=3, starts_at=time(10, 0), ends_at=time(11, 0),
        )
        db.session.add_all([first_session, second_session])
        db.session.flush()
        db.session.add_all([
            Attendance(
                student_id=39, subject_id=48, date=date(2026, 10, 1), status='LATE',
                session_id=first_session.id, marked_by=17,
            ),
            Attendance(
                student_id=39, subject_id=48, date=date(2026, 10, 1), status='EXCUSED',
                session_id=second_session.id, marked_by=17,
            ),
        ])
        db.session.commit()
        assert Attendance.query.filter_by(student_id=39, subject_id=48, date=date(2026, 10, 1)).count() == 3
        duplicate_period = Attendance(
            student_id=39, subject_id=48, date=date(2026, 10, 1), status='ABSENT',
            session_id=first_session.id,
        )
        db.session.add(duplicate_period)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()
        duplicate_daily = Attendance(
            student_id=39, subject_id=48, date=date(2026, 10, 1), status='ABSENT',
        )
        db.session.add(duplicate_daily)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    response = client.post('/login', data={
        'username': 'legacy-admin', 'password': 'migration-test-password',
    })
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/dashboard')
    assert client.get('/admin/dashboard').status_code == 200

    second_upgrade = app.test_cli_runner().invoke(args=['db', 'upgrade'])
    assert second_upgrade.exit_code == 0, second_upgrade.output


def test_student_section_migration_only_backfills_unambiguous_matches(tmp_path, monkeypatch):
    database_path = tmp_path / 'section-backfill.sqlite'
    monkeypatch.setenv('SECRET_KEY', 'test-section-migration-secret')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path.as_posix()}')
    app = create_app()
    old_schema = app.test_cli_runner().invoke(args=['db', 'upgrade', '20261008_05'])
    assert old_schema.exit_code == 0, old_schema.output

    with app.app_context():
        cse = Department(code='CSE', name='Computer Science')
        it = Department(code='IT', name='Information Technology')
        year = AcademicYear(label='2030-2031', starts_on=date(2030, 6, 1), ends_on=date(2031, 5, 31))
        db.session.add_all([cse, it, year])
        db.session.flush()
        semester = Semester(
            academic_year_id=year.id, number=1, starts_on=date(2030, 6, 1), ends_on=date(2030, 12, 31),
        )
        cse_course = Course(department_id=cse.id, code='CS', name='Computer Science')
        it_course = Course(department_id=it.id, code='IT', name='Information Technology')
        db.session.add_all([semester, cse_course, it_course])
        db.session.flush()
        batch_one = Batch(course_id=cse_course.id, academic_year_id=year.id, code='2030A')
        batch_two = Batch(course_id=cse_course.id, academic_year_id=year.id, code='2030B')
        it_batch = Batch(course_id=it_course.id, academic_year_id=year.id, code='2030')
        db.session.add_all([batch_one, batch_two, it_batch])
        db.session.flush()
        unique_section = Section(batch_id=batch_one.id, code='A')
        ambiguous_one = Section(batch_id=batch_one.id, code='B')
        ambiguous_two = Section(batch_id=batch_two.id, code='B')
        same_code_other_department = Section(batch_id=it_batch.id, code='A')
        db.session.add_all([unique_section, ambiguous_one, ambiguous_two, same_code_other_department])
        user_ids = []
        for index in range(1, 5):
            result = db.session.execute(text(
                'INSERT INTO users (username, password_hash, role) '
                'VALUES (:username, :password_hash, :role)'
            ), {
                'username': f'legacy-section-{index}', 'password_hash': 'hash', 'role': 'STUDENT',
            })
            user_ids.append(result.lastrowid)
        subject = Subject(subject_code='CS-MIG', subject_name='Migration Subject', department='CSE', semester='1')
        db.session.add(subject)
        db.session.commit()
        section_ids = {
            'unique': unique_section.id,
            'ambiguous_one': ambiguous_one.id,
            'ambiguous_two': ambiguous_two.id,
            'other_department': same_code_other_department.id,
        }
        subject_id = subject.id
        for index, legacy_section in enumerate(('A', 'B', 'A', 'A'), start=1):
            legacy_department = 'CSE' if index != 3 else 'UNKNOWN'
            db.session.execute(text(
                'INSERT INTO students '
                '(id, register_number, name, email, phone, department, department_id, year, section, semester, user_id) '
                'VALUES (:id, :register, :name, :email, :phone, :department, NULL, :year, :section, :semester, :user_id)'
            ), {
                'id': 100 + index, 'register': f'LEG-{index}', 'name': f'Legacy {index}',
                'email': f'legacy{index}@example.test', 'phone': str(index), 'department': legacy_department,
                'year': '1', 'section': legacy_section, 'semester': '1', 'user_id': user_ids[index - 1],
            })
        db.session.add(Attendance(
            student_id=101, subject_id=subject_id, date=date(2030, 10, 1), status='PRESENT',
        ))
        db.session.commit()

    migrated = app.test_cli_runner().invoke(args=['db', 'upgrade'])
    assert migrated.exit_code == 0, f'{migrated.output}\n{migrated.exception!r}'
    with app.app_context():
        rows = db.session.execute(text(
            'SELECT id, department, section, section_id FROM students ORDER BY id'
        )).all()
        assignments = {row.id: row.section_id for row in rows}
        assert assignments == {
            101: section_ids['unique'],
            102: None,
            103: None,
            104: section_ids['unique'],
        }
        assert [(row.department, row.section) for row in rows] == [
            ('CSE', 'A'), ('CSE', 'B'), ('UNKNOWN', 'A'), ('CSE', 'A'),
        ]
        assert Attendance.query.filter_by(student_id=101).one().status == 'PRESENT'
        section_fk = next(
            item for item in inspect(db.engine).get_foreign_keys('students')
            if item['constrained_columns'] == ['section_id']
        )
        assert section_fk['referred_table'] == 'sections'


def test_hierarchy_constraints_and_nullable_student_department_link():
    app = create_app(testing=True)
    with app.app_context():
        department = Department(code='CSE', name='Computer Science')
        db.session.add(department)
        db.session.flush()
        course = Course(department_id=department.id, code='BTECH-CS', name='Computer Science')
        year = AcademicYear(label='2026-2027', starts_on=date(2026, 6, 1), ends_on=date(2027, 5, 31))
        room = Classroom(code='A-101', capacity=60)
        db.session.add_all([course, year, room])
        db.session.flush()
        semester = Semester(academic_year_id=year.id, number=1, starts_on=date(2026, 6, 1), ends_on=date(2026, 11, 30))
        batch = Batch(course_id=course.id, academic_year_id=year.id, code='2026')
        db.session.add_all([semester, batch])
        db.session.flush()
        section = Section(batch_id=batch.id, code='A')
        db.session.add(section)
        db.session.commit()

        duplicate_department = Department(code='CSE', name='Duplicate CSE')
        db.session.add(duplicate_department)
        try:
            db.session.commit()
            assert False, 'Department code uniqueness must be enforced.'
        except Exception:
            db.session.rollback()

        assert db.session.get(Department, department.id).courses[0].code == 'BTECH-CS'
        assert Course.query.count() == 1
        assert Semester.query.count() == 1
        assert Batch.query.count() == 1
        assert Section.query.count() == 1
        assert Classroom.query.count() == 1
        assert ClassSession.query.count() == 0


def test_role_aliases_and_department_and_resource_scope_policy():
    legacy_admin = SimpleNamespace(id=7, role='admin')
    assert normalize_role('admin') is Role.SUPER_ADMIN
    assert has_role(legacy_admin, Role.SUPER_ADMIN)
    assert can_access(legacy_admin, 'anything:at_all')

    hod = SimpleNamespace(
        id=8, role='HOD',
        faculty_profile=SimpleNamespace(department='CSE', department_id=None),
        student_profile=None,
    )
    assert can_access(hod, 'attendance:manage', department_id='CSE')
    assert not can_access(hod, 'attendance:manage', department_id='IT')
    assert not can_access(hod, 'attendance:manage')

    department_admin = SimpleNamespace(
        id=9, role='DEPARTMENT_ADMIN',
        faculty_profile=SimpleNamespace(department='CSE', department_id=None),
        student_profile=None,
    )
    assert can_access(department_admin, 'student:manage', department_id='cse')
    assert not can_access(department_admin, 'user:manage', department_id='CSE')

    student = SimpleNamespace(id=10, role='STUDENT', faculty_profile=None, student_profile=None)
    assert can_access(student, 'attendance:read_own', resource_owner_id=10)
    assert not can_access(student, 'attendance:read_own', resource_owner_id=11)
    assert not can_access(student, 'attendance:read')
