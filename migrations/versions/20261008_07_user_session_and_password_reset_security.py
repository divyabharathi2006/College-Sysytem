"""Add account status, persistent revocable sessions, and reset tokens.

Revision ID: 20261008_07
Revises: 20261008_06
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_07'
down_revision = '20261008_06'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    user_columns = {column['name'] for column in inspector.get_columns('users')}
    if 'is_enabled' not in user_columns:
        op.add_column(
            'users', sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if 'last_login_at' not in user_columns:
        op.add_column('users', sa.Column('last_login_at', sa.DateTime(), nullable=True))

    tables = set(inspect(bind).get_table_names())
    if 'user_sessions' not in tables:
        op.create_table(
            'user_sessions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('session_id_hash', sa.String(length=64), nullable=False, unique=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
        )
    if 'password_reset_tokens' not in tables:
        op.create_table(
            'password_reset_tokens',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
        )

    for table, name, columns in (
        ('user_sessions', 'ix_user_sessions_user_id', ['user_id']),
        ('user_sessions', 'ix_user_sessions_session_id_hash', ['session_id_hash']),
        ('user_sessions', 'ix_user_sessions_expires_at', ['expires_at']),
        ('password_reset_tokens', 'ix_password_reset_tokens_user_id', ['user_id']),
        ('password_reset_tokens', 'ix_password_reset_tokens_token_hash', ['token_hash']),
        ('password_reset_tokens', 'ix_password_reset_tokens_expires_at', ['expires_at']),
    ):
        indexes = {index['name'] for index in inspect(bind).get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns, unique=False)


def downgrade():
    raise RuntimeError(
        'Account and session security data is retained; this additive migration is intentionally irreversible.'
    )
