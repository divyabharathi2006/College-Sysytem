import re

from models.student import Student


def next_registration_number(prefix):
    if not re.fullmatch(r'DBU3\d{2}', prefix):
        raise ValueError('Registration number prefix is invalid.')

    existing_numbers = Student.query.with_entities(Student.register_number).filter(
        Student.register_number.startswith(prefix),
    ).all()
    used_suffixes = []
    for (register_number,) in existing_numbers:
        suffix = register_number[len(prefix):]
        if suffix.isascii() and suffix.isdigit():
            used_suffixes.append(int(suffix))

    next_suffix = max(used_suffixes, default=0) + 1
    register_number = f'{prefix}{next_suffix}'
    if len(register_number) > 50:
        raise ValueError('Registration number sequence has exceeded its supported length.')
    return register_number
