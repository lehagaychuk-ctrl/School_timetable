"""Local accounts with separate school databases and hashed recovery codes."""
from contextlib import contextmanager
from datetime import timedelta
import re
import secrets
import sqlite3
import time
from pathlib import Path

from flask import abort, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash


def install_accounts(app, database, initialize_school, account_database=None):
    store = Path(account_database or database.with_name(database.stem + '.accounts.sqlite3'))
    store.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect():
        connection = sqlite3.connect(store, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with connect() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS account_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, school TEXT NOT NULL, name TEXT NOT NULL,
              email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, recovery_hash TEXT NOT NULL,
              database_path TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
        ''')
        conn.execute('INSERT OR IGNORE INTO account_settings VALUES (?,?)', ('secret', secrets.token_hex(32)))
        app.secret_key = conn.execute("SELECT value FROM account_settings WHERE key='secret'").fetchone()[0]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    attempts = {}
    dummy_hash = generate_password_hash(secrets.token_urlsafe(24))

    def public(row):
        return {key: row[key] for key in ('id', 'school', 'name', 'email')}

    def email(value):
        value = str(value or '').strip().casefold()
        if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Введите электронную почту в формате name@example.com.')
        return value

    def password(value):
        if not isinstance(value, str) or not 10 <= len(value) <= 128:
            raise ValueError('Пароль должен содержать от 10 до 128 символов.')
        return value

    def limit_attempts():
        now = time.monotonic()
        key = request.remote_addr or 'local'
        recent = [stamp for stamp in attempts.get(key, []) if stamp > now - 300]
        if len(recent) >= 15:
            abort(429, description='Слишком много попыток. Повторите через 5 минут.')
        recent.append(now)
        attempts[key] = recent

    def sign_in(row):
        session.clear()
        session['account_id'], session['account_version'] = row['id'], row['version']
        session.permanent = True

    @app.before_request
    def account_guard():
        g.account = None
        with connect() as conn:
            g.accounts_required = bool(conn.execute('SELECT 1 FROM accounts LIMIT 1').fetchone())
            if session.get('account_id'):
                row = conn.execute('SELECT * FROM accounts WHERE id=?', (session['account_id'],)).fetchone()
                if row and row['version'] == session.get('account_version'):
                    g.account = public(row)
                    g.database_path = Path(row['database_path'])
                else:
                    session.clear()
        public_path = request.path in ('/', '/account') or request.path.startswith(('/static/', '/api/account/'))
        if g.accounts_required and not g.account and not public_path:
            abort(401, description='Войдите в аккаунт, чтобы открыть расписание школы.')

    @app.get('/account')
    def account_page():
        return render_template('account.html', asset_version=(Path(app.root_path) / 'static' / 'account.js').stat().st_mtime_ns)

    @app.get('/api/account/state')
    def account_state():
        return jsonify(user=g.account, required=g.accounts_required, version='school-workspace-2')

    @app.post('/api/account/register')
    def register():
        limit_attempts()
        body = request.get_json()
        address = email(body.get('email'))
        secret = password(body.get('password'))
        school = ' '.join(str(body.get('school', '')).split())
        name = ' '.join(str(body.get('name', '')).split())
        if not 1 <= len(school) <= 160 or not 1 <= len(name) <= 120:
            raise ValueError('Заполните название школы (до 160 символов) и ваше имя (до 120).')
        recovery = secrets.token_urlsafe(24)
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM accounts WHERE email=?', (address,)).fetchone():
                abort(409, description='Аккаунт с этой почтой уже существует.')
            first = not conn.execute('SELECT 1 FROM accounts LIMIT 1').fetchone()
            cursor = conn.execute('INSERT INTO accounts(school,name,email,password_hash,recovery_hash,database_path) VALUES (?,?,?,?,?,?)',
                                  (school, name, address, generate_password_hash(secret), generate_password_hash(recovery), ''))
            account_id = cursor.lastrowid
            target = database if first else database.parent / (database.stem + '-schools') / f'{account_id}.sqlite3'
            if not first:
                initialize_school(target)
            conn.execute('UPDATE accounts SET database_path=? WHERE id=?', (str(target.resolve()), account_id))
            row = conn.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        sign_in(row)
        return jsonify(user=public(row), recovery_code=recovery, existing_school=first), 201

    @app.post('/api/account/login')
    def login():
        limit_attempts()
        body = request.get_json()
        address = email(body.get('email'))
        secret = body.get('password', '')
        if not isinstance(secret, str) or len(secret) > 128:
            abort(401, description='Неверная почта или пароль.')
        with connect() as conn:
            row = conn.execute('SELECT * FROM accounts WHERE email=?', (address,)).fetchone()
        valid = check_password_hash(row['password_hash'] if row else dummy_hash, secret)
        if not row or not valid:
            abort(401, description='Неверная почта или пароль.')
        sign_in(row)
        return jsonify(user=public(row))

    @app.post('/api/account/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post('/api/account/reset')
    def reset_password():
        limit_attempts()
        body = request.get_json()
        address = email(body.get('email'))
        new_password = password(body.get('password'))
        code = str(body.get('recovery_code', ''))[:128]
        replacement = secrets.token_urlsafe(24)
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM accounts WHERE email=?', (address,)).fetchone()
            valid = check_password_hash(row['recovery_hash'] if row else dummy_hash, code)
            if not row or not valid:
                abort(401, description='Неверная почта или код восстановления.')
            conn.execute('UPDATE accounts SET password_hash=?,recovery_hash=?,version=version+1 WHERE id=?',
                         (generate_password_hash(new_password), generate_password_hash(replacement), row['id']))
        session.clear()
        return jsonify(ok=True, recovery_code=replacement)

