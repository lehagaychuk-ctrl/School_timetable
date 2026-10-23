"""Local school timetable editor. Run with Python 3.12+ (see README.md)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.vendor'))

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from flask import Flask, jsonify, request, send_file, render_template, abort, g, has_request_context
from scheduling import normalized, parse_time, week_dates, candidate, teacher_rule_issues, day_bells
from planning import initialize_planning, sync_catalogs, planning_rule_issues

DEFAULT_TEMPLATE = {
    'name': 'Школьный стандарт', 'paper': 'A4', 'orientation': 'landscape',
    'classes_per_table': 6, 'font_size': 9, 'column_width': 115, 'row_height': 27,
    'show_teachers': False, 'show_rooms': True, 'show_approval': True,
    'school_name': '', 'director': '', 'approval_text': 'Утверждаю \n Директор школы',
    'between_notes': '',
    'layout': 'standard', 'group_by_grade': False, 'show_page_numbers': True,
    'approval_first_shift_only': False,
}

REFERENCE_TEMPLATE = {
    **DEFAULT_TEMPLATE, 'name': 'По образцу — компактные таблицы',
    'layout': 'reference', 'classes_per_table': 9, 'font_size': 8,
    'column_width': 110, 'row_height': 12, 'group_by_grade': True,
    'show_page_numbers': False, 'approval_first_shift_only': True,
}


def default_bells():
    return {
        '1': ['08:00–08:40', '08:45–09:25', '09:45–10:25', '10:45–11:25',
              '11:35–12:15', '12:25–13:05', '13:15–13:55', '14:05–14:45'],
        '2': ['14:00–14:40', '14:45–15:25', '15:35–16:15', '16:25–17:05',
              '17:15–17:55', '18:05–18:45', '18:55–19:35', '19:45–20:25'],
    }


def empty_day():
    return {'lessons': [], 'notes': {'1': '', '2': ''}, 'print_notes': [], 'approved': False}


def clean_teacher(body, classes=()):
    if not isinstance(body, dict):
        raise ValueError('Некорректная карточка учителя.')
    name = ' '.join(str(body.get('name', '')).split())
    if not 1 <= len(name) <= 120:
        raise ValueError('Введите ФИО учителя длиной до 120 символов.')
    result = {'name': name, 'note': str(body.get('note', '')).strip(), 'active': body.get('active', True)}
    if len(result['note']) > 1000 or not isinstance(result['active'], bool):
        raise ValueError('Примечание — до 1000 символов. Статус карточки должен быть логическим значением.')
    for key, maximum, length in [('subjects', 30, 120), ('rooms', 4, 40)]:
        values = body.get(key, [])
        if isinstance(values, str):
            values = re.split(r'[,;\n]', values)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError('Предметы и кабинеты должны быть списками или строками через запятую.')
        seen, cleaned = set(), []
        for value in values:
            value = ' '.join(value.split())
            if not value:
                continue
            if len(value) > length:
                raise ValueError(f'Поле {key}: длина одного значения — до {length} символов.')
            if normalized(value) not in seen:
                cleaned.append(value)
                seen.add(normalized(value))
        if len(cleaned) > maximum:
            raise ValueError(f'Поле {key}: допускается до {maximum} значений.')
        result[key] = cleaned
    class_ids = body.get('class_ids', [])
    if not isinstance(class_ids, list):
        raise ValueError('Выберите классы из списка.')
    known = {c['id'] for c in classes}
    result['class_ids'] = sorted(set(int(cid) for cid in class_ids))
    if any(cid not in known for cid in result['class_ids']):
        raise ValueError('В карточке учителя указан неизвестный класс.')
    def hours(value, title, maximum=84):
        if value in (None, ''):
            return None
        if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)) or not 0 <= int(value) <= maximum:
            raise ValueError(f'{title}: укажите целое число от 0 до {maximum}.')
        return int(value)
    result['weekly_hours'] = hours(body.get('weekly_hours'), 'Недельная нагрузка')
    result['daily_limit'] = hours(body.get('daily_limit'), 'Предел уроков на день', 12)
    class_hours = body.get('class_hours', {})
    if not isinstance(class_hours, dict):
        raise ValueError('Некорректная нагрузка по классам.')
    result['class_hours'] = {}
    for cid, value in class_hours.items():
        if int(cid) not in result['class_ids']:
            raise ValueError('Нагрузку можно указать только для выбранного класса.')
        target = hours(value, 'Нагрузка класса')
        if target is not None:
            result['class_hours'][str(int(cid))] = target
    result['availability_enabled'] = body.get('availability_enabled', False)
    if not isinstance(result['availability_enabled'], bool):
        raise ValueError('Некорректный переключатель часов работы.')
    availability = body.get('availability', {})
    if not isinstance(availability, dict) or any(str(k) not in [str(i) for i in range(7)] for k in availability):
        raise ValueError('Часы работы задаются для дней недели с понедельника по воскресенье.')
    result['availability'] = {}
    for weekday in range(7):
        values = availability.get(str(weekday), [])
        if isinstance(values, str):
            values = [v.strip() for v in re.split(r'[;\n]', values) if v.strip()]
        if not isinstance(values, list) or len(values) > 6:
            raise ValueError('Для одного дня можно указать до шести интервалов работы.')
        intervals = sorted(parse_time(v) for v in values)
        merged = []
        for start, end in intervals:
            if merged and start < merged[-1][1]:
                raise ValueError('Интервалы часов работы одного дня не должны пересекаться.')
            if merged and start == merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        result['availability'][str(weekday)] = [f'{s//60:02}:{s%60:02}–{e//60:02}:{e%60:02}' for s, e in merged]
    return result


def read_teachers(conn):
    teachers = []
    for row in conn.execute('SELECT * FROM teachers ORDER BY name_key,id'):
        teachers.append({'id': row['id'], 'name': row['name'], 'subjects': json.loads(row['subjects']),
                         'rooms': json.loads(row['rooms']), 'note': row['note'],
                         'active': bool(row['active']), 'revision': row['revision'],
                         'class_ids': json.loads(row['class_ids']), 'class_hours': json.loads(row['class_hours']),
                         'weekly_hours': row['weekly_hours'], 'daily_limit': row['daily_limit'],
                         'availability_enabled': bool(row['availability_enabled']), 'availability': json.loads(row['availability'])})
    return teachers


def resolve_teachers(day, teachers):
    """Resolve stable references; old free-text entries remain readable."""
    ids = {t['id']: t for t in teachers}
    names = {normalized(t['name']): t for t in teachers}
    for lesson in day['lessons']:
        for group in lesson['groups']:
            teacher = ids.get(group.get('teacher_id')) or names.get(normalized(group.get('teacher', '')))
            if teacher:
                group.update(teacher_id=teacher['id'], teacher=teacher['name'], teacher_archived=not teacher['active'])
    return day


def validate_template(value):
    result = deepcopy(DEFAULT_TEMPLATE)
    result.update({k: v for k, v in value.items() if k in result})
    if result['layout'] not in ('standard', 'reference'):
        raise ValueError('Выберите стандартное оформление или оформление по образцу.')
    if result['paper'] not in ('A4', 'A3') or result['orientation'] not in ('landscape', 'portrait'):
        raise ValueError('Неизвестный формат страницы.')
    for key, lo, hi in [('classes_per_table', 1, 10), ('font_size', 6, 16),
                        ('column_width', 45, 240), ('row_height', 8, 100)]:
        result[key] = float(result[key]) if key != 'classes_per_table' else int(result[key])
        if not lo <= result[key] <= hi:
            raise ValueError(f'Параметр {key} должен быть от {lo} до {hi}.')
    for key in ('name', 'school_name', 'director', 'approval_text', 'between_notes'):
        result[key] = str(result[key]).strip()
        if len(result[key]) > (2000 if key == 'between_notes' else 250):
            raise ValueError('Слишком длинное поле шаблона.')
    for key in ('show_teachers', 'show_rooms', 'show_approval', 'group_by_grade', 'show_page_numbers', 'approval_first_shift_only'):
        if not isinstance(result[key], bool):
            raise ValueError('Переключатели шаблона должны иметь логическое значение.')
    if not result['name']:
        raise ValueError('Введите название шаблона.')
    return result


def clean_day(body, classes, bells, teachers=()):
    if not isinstance(body, dict) or not isinstance(body.get('lessons'), list):
        raise ValueError('Некорректное расписание.')
    overrides = body.get('bells', {})
    if not isinstance(overrides, dict) or any(s not in ('1', '2') for s in overrides):
        raise ValueError('Звонки на дату задаются отдельно для первой и второй смены.')
    cleaned_bells = {}
    for shift, values in overrides.items():
        if not isinstance(values, dict) or not 1 <= len(values) <= 13:
            raise ValueError('Задайте от 1 до 13 звонков на дату, с номерами 0–12.')
        if any(not re.fullmatch(r'(?:[0-9]|1[0-2])', str(slot)) or not isinstance(value, str) for slot, value in values.items()):
            raise ValueError('Номера уроков для звонков на дату: 0–12.')
        previous_end = -1
        cleaned_bells[shift] = {}
        for slot, value in sorted(values.items(), key=lambda pair: int(pair[0])):
            start, end = parse_time(value)
            if start < previous_end:
                raise ValueError('Звонки на дату должны идти по порядку и не пересекаться.')
            cleaned_bells[shift][str(slot)] = f'{start//60:02}:{start%60:02}–{end//60:02}:{end%60:02}'
            previous_end = end
    ids = {c['id']: c for c in classes}
    seen, lessons = set(), []
    for item in body['lessons']:
        cid, slot = int(item['class_id']), int(item['slot'])
        if cid not in ids or not 0 <= slot <= 12:
            raise ValueError('Неизвестный класс или номер урока (допускаются номера 0–12).')
        if slot == 0 and 0 not in day_bells({'bells': cleaned_bells}, bells, ids[cid]['shift']):
            raise ValueError('Сначала задайте время нулевого урока в звонках на выбранную дату.')
        if (cid, slot) in seen:
            raise ValueError('Для одного класса задано несколько записей на один урок. Используйте подгруппы.')
        seen.add((cid, slot))
        groups = item.get('groups', [])
        if not isinstance(groups, list) or len(groups) > 4:
            raise ValueError('Допускается до четырёх подгрупп в одном уроке.')
        cleaned = []
        for group in groups:
            g = {k: str(group.get(k, '')).strip() for k in ('subject', 'teacher', 'label')}
            if group.get('teacher_id') not in (None, ''):
                teacher_id = int(group['teacher_id'])
                teacher = next((t for t in teachers if t['id'] == teacher_id), None)
                if not teacher:
                    raise ValueError('Учитель не найден в каталоге. Обновите страницу и выберите его заново.')
                g.update(teacher_id=teacher_id, teacher=teacher['name'])
            if len(g['subject']) > 120 or len(g['teacher']) > 120 or len(g['label']) > 40:
                raise ValueError('Название предмета и ФИО — до 120 символов, название группы — до 40.')
            rooms = group.get('rooms', [])
            if isinstance(rooms, str):
                rooms = re.split(r'[,;]', rooms)
            if not isinstance(rooms, list):
                raise ValueError('Кабинеты должны быть списком или строкой через запятую.')
            g['rooms'] = list(dict.fromkeys(str(r).strip() for r in rooms if str(r).strip()))
            if len(g['rooms']) > 4 or any(len(r) > 40 for r in g['rooms']):
                raise ValueError('Укажите не более четырёх кабинетов длиной до 40 символов.')
            if g['subject'] or g['teacher'] or g['rooms']:
                cleaned.append(g)
        cancelled = bool(item.get('cancelled', False))
        note = str(item.get('note', '')).strip()
        if len(note) > 250:
            raise ValueError('Примечание к уроку — до 250 символов.')
        if cleaned or cancelled or note:
            lessons.append({'class_id': cid, 'slot': slot, 'groups': cleaned,
                            'cancelled': cancelled, 'note': note})
    notes = body.get('notes', {})
    notes = {str(s): str(notes.get(str(s), '')).strip() for s in (1, 2)}
    if any(len(v) > 4000 for v in notes.values()):
        raise ValueError('Примечание к смене — до 4000 символов.')
    print_notes = body.get('print_notes', [])
    if not isinstance(print_notes, list) or len(print_notes) > len(classes):
        raise ValueError('Допускается не более одного объявления на класс.')
    cleaned_notes, seen_notes = [], set()
    for item in print_notes:
        if not isinstance(item, dict) or type(item.get('class_id')) is not int or item['class_id'] not in ids:
            raise ValueError('Выберите класс, перед таблицей которого нужно объявление.')
        if item['class_id'] in seen_notes:
            raise ValueError('Для одного класса можно задать одно объявление перед таблицей.')
        text = str(item.get('text', '')).strip()
        if len(text) > 2000:
            raise ValueError('Объявление перед таблицей — до 2000 символов.')
        cleaned_notes.append({'class_id': item['class_id'], 'text': text})
        seen_notes.add(item['class_id'])
    result = {'lessons': lessons, 'notes': notes, 'print_notes': cleaned_notes, 'approved': False}
    if cleaned_bells:
        result['bells'] = cleaned_bells
    return resolve_teachers(result, teachers)


def conflicts(day, classes, bells):
    issues, entries = [], []
    ids = {c['id']: c for c in classes}
    active = 0
    for lesson in day['lessons']:
        cls = ids.get(lesson['class_id'])
        if not cls or lesson.get('cancelled'):
            continue
        slot = lesson['slot']
        label = f"{cls['name']}, урок {slot}"
        cells = [{'class_id': cls['id'], 'slot': slot}]
        time = day_bells(day, bells, cls['shift']).get(slot)
        if not time:
            issues.append({'severity': 'error', 'message': f'{label}: нет времени в расписании звонков.', 'cells': cells})
            continue
        start, end = parse_time(time)
        for index, group in enumerate(lesson['groups']):
            active += 1
            if not group['subject']:
                issues.append({'severity': 'error', 'message': f'{label}: не указан предмет.', 'cells': cells})
            for field, title in [('teacher', 'учитель'), ('rooms', 'кабинет')]:
                if not group[field]:
                    issues.append({'severity': 'warning', 'message': f'{label}: не указан {title}.', 'cells': cells})
            if group.get('teacher_archived'):
                issues.append({'severity': 'warning', 'message': f'{label}: учитель {group["teacher"]} находится в архиве каталога.', 'cells': cells})
            entries.append({'label': label, 'class_id': cls['id'], 'slot': slot, 'group': index,
                            'start': start, 'end': end, 'teacher': normalized(group['teacher']),
                            'teacher_key': ('id', group['teacher_id']) if group.get('teacher_id') else ('name', normalized(group['teacher'])),
                            'rooms': {normalized(r) for r in group['rooms']}})
    if not active:
        issues.append({'severity': 'error', 'message': 'В расписании нет действующих уроков. Заполните расписание перед утверждением.', 'target': 'constructor'})
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            if a['start'] >= b['end'] or b['start'] >= a['end']:
                continue
            cells = [{'class_id': item['class_id'], 'slot': item['slot']} for item in (a, b)]
            if a['class_id'] == b['class_id'] and a['slot'] != b['slot']:
                issues.append({'severity': 'error', 'message': f'Класс занят одновременно на двух уроках: {a["label"]} и {b["label"]}.',
                               'class_ids': [a['class_id']], 'cells': cells})
            for kind, matches in [('Учитель', a['teacher'] and a['teacher_key'] == b['teacher_key']),
                                  ('Кабинет', bool(a['rooms'] & b['rooms']))]:
                if matches:
                    resource = a['teacher'] if kind == 'Учитель' else ', '.join(sorted(a['rooms'] & b['rooms']))
                    issues.append({'severity': 'error', 'message': f'{kind} {resource}: пересечение занятий — {a["label"]} и {b["label"]} (включая подгруппы).',
                                   'class_ids': [a['class_id'], b['class_id']], 'cells': cells})
    return issues


def create_app(db_path=None, *, seed_classes=True, _initialize_only=False):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    database = Path(db_path or os.environ.get('TIMETABLE_DB', ROOT / 'data' / 'school.sqlite3'))
    database.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def db(path=None):
        target = path or (getattr(g, 'database_path', database) if has_request_context() else database)
        conn = sqlite3.connect(target, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with db(database) as conn:
        had_data = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='days'").fetchone()
        has_catalog = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='teachers'").fetchone()
        old_columns = {r['name'] for r in conn.execute('PRAGMA table_info(teachers)')} if has_catalog else set()
        old_class_columns = {r['name'] for r in conn.execute('PRAGMA table_info(classes)')}
        if had_data and ('class_ids' not in old_columns or 'print_group' not in old_class_columns) and db_path is None:
            backup_dir = database.parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now(timezone(timedelta(hours=3))).strftime('%Y%m%d-%H%M%S-%f')
            backup = sqlite3.connect(backup_dir / f'before-schema-update-{stamp}.sqlite3')
            try:
                conn.backup(backup)
            finally:
                backup.close()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
              shift INTEGER NOT NULL CHECK(shift IN (1,2)), position INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS days(date TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS weekly(weekday INTEGER PRIMARY KEY CHECK(weekday BETWEEN 0 AND 6), body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS templates(name TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS teachers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              name_key TEXT NOT NULL UNIQUE, subjects TEXT NOT NULL, rooms TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
              revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);
        ''')
        if 'print_group' not in {r['name'] for r in conn.execute('PRAGMA table_info(classes)')}:
            conn.execute("ALTER TABLE classes ADD COLUMN print_group TEXT NOT NULL DEFAULT ''")
        rule_columns = {'class_ids': "TEXT NOT NULL DEFAULT '[]'", 'class_hours': "TEXT NOT NULL DEFAULT '{}'",
                        'weekly_hours': 'INTEGER', 'daily_limit': 'INTEGER',
                        'availability_enabled': 'INTEGER NOT NULL DEFAULT 0', 'availability': "TEXT NOT NULL DEFAULT '{}'"}
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(teachers)')}
        for column, definition in rule_columns.items():
            if column not in columns:
                conn.execute(f'ALTER TABLE teachers ADD COLUMN {column} {definition}')
        if seed_classes and not conn.execute('SELECT 1 FROM classes LIMIT 1').fetchone():
            conn.executemany('INSERT INTO classes(id,name,shift,position) VALUES (?,?,?,?)',
                             [(i, f'Класс {i:02}', 1 if i <= 24 else 2, i) for i in range(1, 48)])
        for key, value in [('template', DEFAULT_TEMPLATE), ('bells', default_bells())]:
            conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
        conn.execute('INSERT OR IGNORE INTO templates VALUES (?,?)',
                     (DEFAULT_TEMPLATE['name'], json.dumps(DEFAULT_TEMPLATE, ensure_ascii=False)))
        conn.execute('INSERT OR IGNORE INTO templates VALUES (?,?)',
                     (REFERENCE_TEMPLATE['name'], json.dumps(REFERENCE_TEMPLATE, ensure_ascii=False)))
        if not conn.execute("SELECT 1 FROM migrations WHERE name='teacher_catalog_v1'").fetchone():
            discovered = {}
            for table in ('days', 'weekly'):
                for row in conn.execute(f'SELECT body FROM {table}'):
                    for lesson in json.loads(row['body'])['lessons']:
                        for group in lesson['groups']:
                            name = str(group.get('teacher', '')).strip()
                            if not name:
                                continue
                            key = normalized(name)
                            item = discovered.setdefault(key, {'name': name, 'subjects': [], 'rooms': []})
                            subject = group.get('subject', '')
                            if subject and subject not in item['subjects']:
                                item['subjects'].append(subject)
                            for room in group.get('rooms', []):
                                if room not in item['rooms']:
                                    item['rooms'].append(room)
            for key, item in discovered.items():
                conn.execute('INSERT OR IGNORE INTO teachers(name,name_key,subjects,rooms) VALUES (?,?,?,?)',
                             (item['name'], key, json.dumps(item['subjects'][:30], ensure_ascii=False),
                              json.dumps(item['rooms'][:4], ensure_ascii=False)))
            catalog = read_teachers(conn)
            for table, primary in [('days', 'date'), ('weekly', 'weekday')]:
                for row in conn.execute(f'SELECT {primary},body FROM {table}').fetchall():
                    body = resolve_teachers(json.loads(row['body']), catalog)
                    encoded = json.dumps(body, ensure_ascii=False)
                    if encoded != row['body']:
                        extra = ',revision=revision+1' if table == 'days' else ''
                        conn.execute(f'UPDATE {table} SET body=?{extra} WHERE {primary}=?', (encoded, row[primary]))
            conn.execute("INSERT INTO migrations VALUES ('teacher_catalog_v1')")
        if not conn.execute("SELECT 1 FROM migrations WHERE name='teacher_working_rules_v1'").fetchone():
            inferred = {}
            for table in ('days', 'weekly'):
                for row in conn.execute(f'SELECT body FROM {table}'):
                    for lesson in resolve_teachers(json.loads(row['body']), read_teachers(conn))['lessons']:
                        for group in lesson['groups']:
                            if group.get('teacher_id'):
                                inferred.setdefault(group['teacher_id'], set()).add(lesson['class_id'])
            for teacher_id, ids in inferred.items():
                conn.execute('UPDATE teachers SET class_ids=? WHERE id=? AND class_ids=?',
                             (json.dumps(sorted(ids)), teacher_id, '[]'))
            conn.execute("INSERT INTO migrations VALUES ('teacher_working_rules_v1')")
        initialize_planning(conn)

    if _initialize_only:
        return app

    def read_state(conn):
        classes = [dict(r) for r in conn.execute('SELECT * FROM classes ORDER BY position,id')]
        settings = {r['key']: json.loads(r['body']) for r in conn.execute('SELECT * FROM settings')}
        settings['template'] = validate_template(settings['template'])
        return classes, settings

    def checked_date(value):
        value = str(value or '')
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError('Выберите дату в формате ГГГГ-ММ-ДД.')
        if parsed.isoformat() != value:
            raise ValueError('Некорректная дата.')
        return value

    def get_day(conn, day_date):
        row = conn.execute('SELECT * FROM days WHERE date=?', (day_date,)).fetchone()
        return (resolve_teachers(json.loads(row['body']), read_teachers(conn)), row['revision']) if row else (empty_day(), 0)

    def week_state(conn, day_date, override=None):
        teachers = read_teachers(conn)
        templates = {r['weekday']: json.loads(r['body']) for r in conn.execute('SELECT * FROM weekly')}
        days = {}
        sources = {}
        for value in week_dates(day_date):
            row = conn.execute('SELECT body FROM days WHERE date=?', (value,)).fetchone()
            if value == day_date and override is not None:
                body, source = deepcopy(override), 'day'
            elif row:
                body, source = json.loads(row['body']), 'day'
            elif date.fromisoformat(value).weekday() in templates:
                body, source = deepcopy(templates[date.fromisoformat(value).weekday()]), 'weekly'
            else:
                body, source = empty_day(), 'empty'
            days[value] = resolve_teachers(body, teachers)
            sources[value] = source
        return days, sources

    def check_day(conn, day_date, body, classes, settings):
        week, _ = week_state(conn, day_date, body)
        return (conflicts(body, classes, settings['bells']) + teacher_rule_issues(day_date, body, read_teachers(conn), classes, settings['bells'], week)
                + planning_rule_issues(day_date, body, classes, settings))

    def constructor_state(conn, day_date, class_id, slot, group_index=None):
        classes, settings = read_state(conn)
        cls = next((c for c in classes if c['id'] == class_id), None)
        if not cls:
            raise ValueError('Выберите существующий класс.')
        body, revision = get_day(conn, day_date)
        lesson = next((l for l in body['lessons'] if l['class_id'] == class_id and l['slot'] == slot), None)
        if group_index is not None:
            if type(group_index) is not int or group_index < 0:
                raise ValueError('Выберите существующую подгруппу урока.')
            if (lesson and group_index >= len(lesson['groups'])) or (not lesson and group_index != 0):
                abort(409, description='Состав урока изменился. Обновите список преподавателей.')
        time = day_bells(body, settings['bells'], cls['shift']).get(slot)
        if not time:
            raise ValueError('Выберите урок из расписания звонков класса.')
        # Replacing one group must not count its current teacher as already busy,
        # or charge the same lesson twice against daily and weekly limits.
        evaluation = deepcopy(body)
        if group_index is not None and lesson:
            current = next(l for l in evaluation['lessons'] if l['class_id'] == class_id and l['slot'] == slot)
            current['groups'].pop(group_index)
            if not current['groups']:
                evaluation['lessons'].remove(current)
        week, sources = week_state(conn, day_date, evaluation)
        all_candidates = [candidate(t, day_date, cls, slot, evaluation, classes, settings['bells'], week) for t in read_teachers(conn)]
        candidates = [c for c in all_candidates if c['teacher']['active']]
        return {'class_info': cls, 'slot': slot, 'time': time,
                'candidates': candidates, 'busy_teachers': [c for c in all_candidates if c['busy_lessons']],
                'revision': revision, 'date': day_date,
                'occupied': lesson is not None, 'group_index': group_index,
                'week_start': week_dates(day_date)[0], 'week_end': week_dates(day_date)[-1], 'week_sources': sources}

    def sync_teacher(conn, teacher, old_name=None, invalidate=False):
        keys = {normalized(teacher['name']), normalized(old_name or teacher['name'])}
        for table, primary in [('days', 'date'), ('weekly', 'weekday')]:
            for row in conn.execute(f'SELECT {primary},body FROM {table}').fetchall():
                body = json.loads(row['body'])
                affected = False
                for lesson in body['lessons']:
                    for group in lesson['groups']:
                        if group.get('teacher_id') == teacher['id'] or (not group.get('teacher_id') and normalized(group.get('teacher', '')) in keys):
                            group.update(teacher_id=teacher['id'], teacher=teacher['name'], teacher_archived=not teacher['active'])
                            affected = True
                encoded = json.dumps(body, ensure_ascii=False)
                if affected and (invalidate or encoded != row['body']):
                    if table == 'days':
                        body['approved'] = False
                    encoded = json.dumps(body, ensure_ascii=False)
                    extra = ',revision=revision+1' if table == 'days' else ''
                    conn.execute(f'UPDATE {table} SET body=?{extra} WHERE {primary}=?', (encoded, row[primary]))

    def write_day(conn, day_date, body, expected):
        _, revision = get_day(conn, day_date)
        if expected is not None and int(expected) != revision:
            abort(409, description='Расписание изменилось в другом окне. Обновите страницу перед сохранением.')
        revision += 1
        conn.execute('INSERT INTO days VALUES (?,?,?) ON CONFLICT(date) DO UPDATE SET body=excluded.body,revision=excluded.revision',
                     (day_date, json.dumps(body, ensure_ascii=False), revision))
        return revision

    @app.before_request
    def local_write_guard():
        if request.method in ('POST', 'PUT', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                abort(403, description='Запрос из другого сайта запрещён.')
            if not request.is_json:
                abort(415, description='Ожидается JSON.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.path in ('/', '/account') or request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(415)
    @app.errorhandler(422)
    @app.errorhandler(429)
    def http_error(error):
        return jsonify(error=error.description), error.code

    from accounts import install_accounts
    install_accounts(app, database, lambda path: create_app(path, seed_classes=False, _initialize_only=True))

    @app.get('/')
    def index():
        if g.accounts_required and not g.account:
            return render_template('account.html', asset_version=(ROOT / 'static' / 'account.js').stat().st_mtime_ns)
        return render_template('index.html', asset_version=(ROOT / 'static' / 'app.js').stat().st_mtime_ns)

    @app.get('/api/state')
    def state():
        with db() as conn:
            classes, settings = read_state(conn)
            templates = [validate_template(json.loads(r['body'])) for r in conn.execute('SELECT * FROM templates ORDER BY name')]
            teachers = read_teachers(conn)
        return jsonify(classes=classes, settings=settings, templates=templates, teachers=teachers,
                       account=g.account,
                       capabilities={'teacher_schedule': True, 'busy_teacher_tab': True,
                                     'class_creation': True, 'saved_schedule_picker': True, 'reference_print_layout': True,
                                     'planning_workspace': True, 'accounts': True, 'lesson_picker': True},
                       reference_template=REFERENCE_TEMPLATE,
                       today=datetime.now(timezone(timedelta(hours=3))).date().isoformat())

    @app.post('/api/teachers')
    def add_teacher():
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            classes, _ = read_state(conn)
            value = clean_teacher(request.get_json(), classes)
            if conn.execute('SELECT 1 FROM teachers WHERE name_key=?', (normalized(value['name']),)).fetchone():
                abort(409, description='Учитель с таким ФИО уже есть в каталоге, включая архив.')
            cursor = conn.execute('INSERT INTO teachers(name,name_key,subjects,rooms,note,active) VALUES (?,?,?,?,?,?)',
                                  (value['name'], normalized(value['name']), json.dumps(value['subjects'], ensure_ascii=False),
                                   json.dumps(value['rooms'], ensure_ascii=False), value['note'], int(value['active'])))
            teacher = next(t for t in read_teachers(conn) if t['id'] == cursor.lastrowid)
            save_teacher_rules(conn, teacher['id'], value)
            teacher = next(t for t in read_teachers(conn) if t['id'] == cursor.lastrowid)
            sync_teacher(conn, teacher)
            sync_catalogs(conn)
        return jsonify(teacher=teacher), 201

    @app.put('/api/teachers/<int:teacher_id>')
    def update_teacher(teacher_id):
        payload = request.get_json()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            classes, _ = read_state(conn)
            value = clean_teacher(payload, classes)
            current = next((t for t in read_teachers(conn) if t['id'] == teacher_id), None)
            if not current:
                abort(404, description='Учитель не найден.')
            if int(payload.get('revision', 0)) != current['revision']:
                abort(409, description='Карточка учителя изменилась в другом окне. Обновите страницу.')
            if conn.execute('SELECT 1 FROM teachers WHERE name_key=? AND id!=?', (normalized(value['name']), teacher_id)).fetchone():
                abort(409, description='Учитель с таким ФИО уже есть в каталоге, включая архив.')
            conn.execute('UPDATE teachers SET name=?,name_key=?,subjects=?,rooms=?,note=?,active=?,revision=revision+1 WHERE id=?',
                         (value['name'], normalized(value['name']), json.dumps(value['subjects'], ensure_ascii=False),
                          json.dumps(value['rooms'], ensure_ascii=False), value['note'], int(value['active']), teacher_id))
            save_teacher_rules(conn, teacher_id, value)
            teacher = next(t for t in read_teachers(conn) if t['id'] == teacher_id)
            changed = any(value[k] != current[k] for k in ('name', 'active', 'class_ids', 'class_hours', 'weekly_hours', 'daily_limit', 'availability_enabled', 'availability'))
            sync_teacher(conn, teacher, current['name'], changed)
            sync_catalogs(conn)
        return jsonify(teacher=teacher)

    def save_teacher_rules(conn, teacher_id, value):
        conn.execute('UPDATE teachers SET class_ids=?,class_hours=?,weekly_hours=?,daily_limit=?,availability_enabled=?,availability=? WHERE id=?',
                     (json.dumps(value['class_ids']), json.dumps(value['class_hours']), value['weekly_hours'], value['daily_limit'],
                      int(value['availability_enabled']), json.dumps(value['availability']), teacher_id))

    @app.get('/api/constructor/<day_date>')
    def read_constructor(day_date):
        checked_date(day_date)
        group_index = int(request.args['group_index']) if 'group_index' in request.args else None
        with db() as conn:
            result = constructor_state(conn, day_date, int(request.args.get('class_id', 0)), int(request.args.get('slot', 1)), group_index)
        return jsonify(result)

    @app.post('/api/constructor/<day_date>')
    def assign_constructor(day_date):
        checked_date(day_date)
        payload = request.get_json()
        cid, slot, tid = int(payload['class_id']), int(payload['slot']), int(payload['teacher_id'])
        group_index = payload.get('group_index')
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            context = constructor_state(conn, day_date, cid, slot, group_index)
            if int(payload.get('revision', -1)) != context['revision']:
                abort(409, description='Расписание изменилось. Обновите список свободных учителей.')
            if context['occupied'] and group_index is None:
                abort(409, description='Ячейка занята. Измените существующий урок или выберите свободное время.')
            choice = next((c for c in context['candidates'] if c['teacher']['id'] == tid), None)
            if not choice or not choice['available']:
                return jsonify(error='Учитель недоступен для этого урока.', reasons=choice['reasons'] if choice else ['Учитель отсутствует в действующем каталоге.']), 422
            teacher = choice['teacher']
            subject = str(payload.get('subject', '')).strip()
            if not subject or normalized(subject) not in {normalized(s) for s in teacher['subjects']}:
                raise ValueError('Выберите предмет из карточки этого учителя.')
            body, _ = get_day(conn, day_date)
            group = {'teacher_id': tid, 'teacher': teacher['name'], 'subject': subject, 'rooms': payload.get('rooms', []), 'label': ''}
            if context['occupied']:
                lesson = next(l for l in body['lessons'] if l['class_id'] == cid and l['slot'] == slot)
                group['label'] = lesson['groups'][group_index].get('label', '')
                lesson['groups'][group_index] = group
                lesson['cancelled'] = False
            else:
                body['lessons'].append({'class_id': cid, 'slot': slot, 'groups': [group], 'note': '', 'cancelled': False})
            classes, settings = read_state(conn)
            body = clean_day(body, classes, settings['bells'], read_teachers(conn))
            issues = check_day(conn, day_date, body, classes, settings)
            # Block new errors involving the selected class; existing errors in other
            # cells remain visible without preventing independent corrections.
            old_body, _ = get_day(conn, day_date)
            previous_errors = {i['message'] for i in check_day(conn, day_date, old_body, classes, settings) if i['severity'] == 'error'}
            new_errors = [i for i in issues if i['severity'] == 'error' and i['message'] not in previous_errors]
            if new_errors:
                return jsonify(error='Урок не добавлен: обнаружены конфликты.', issues=new_errors), 422
            revision = write_day(conn, day_date, body, payload['revision'])
            sync_catalogs(conn)
        return jsonify(day=body, revision=revision, issues=issues)

    @app.put('/api/classes')
    def save_classes():
        incoming = request.get_json()['classes']
        if not isinstance(incoming, list) or not incoming or any(not isinstance(c, dict) for c in incoming):
            raise ValueError('Передайте список классов. В школе должен оставаться хотя бы один класс.')
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current, settings = read_state(conn)
            existing_ids = [c['id'] for c in incoming if c.get('id') is not None]
            if any(type(cid) is not int for cid in existing_ids) or len(set(existing_ids)) != len(existing_ids) or set(existing_ids) != {c['id'] for c in current}:
                abort(409, description='Список классов изменился или содержит неверные идентификаторы. Обновите страницу; сохранённые классы нельзя удалять.')
            names = [' '.join(str(c['name']).split()) for c in incoming]
            if any(not n or len(n) > 40 for n in names) or len(set(map(normalized, names))) != len(names):
                raise ValueError('Названия классов должны быть уникальны и содержать от 1 до 40 символов.')
            for c in incoming:
                if int(c['shift']) not in (1, 2):
                    raise ValueError('Допустимы смены 1 и 2.')
                if not isinstance(c.get('print_group', ''), str) or len(c.get('print_group', '').strip()) > 60:
                    raise ValueError('Название таблицы печати — до 60 символов.')
            # Temporary unique names permit swapping two existing names in one save.
            prefix = '__rename_' + os.urandom(16).hex() + '_'
            for c in current:
                conn.execute('UPDATE classes SET name=? WHERE id=?', (f'{prefix}{c["id"]}', c['id']))
            for pos, c in enumerate(incoming):
                if c.get('id') is None:
                    conn.execute('INSERT INTO classes(name,shift,position,print_group) VALUES (?,?,?,?)',
                                 (names[pos], int(c['shift']), pos, c.get('print_group', '').strip()))
                else:
                    group = c.get('print_group', next(old['print_group'] for old in current if old['id'] == c['id']))
                    conn.execute('UPDATE classes SET name=?,shift=?,position=?,print_group=? WHERE id=?',
                                 (names[pos], int(c['shift']), pos, group.strip(), c['id']))
            # A shift change alters conflict checks, so existing approvals must be renewed.
            for row in conn.execute('SELECT date,body FROM days').fetchall():
                body = json.loads(row['body'])
                body['approved'] = False
                conn.execute('UPDATE days SET body=?,revision=revision+1 WHERE date=?', (json.dumps(body, ensure_ascii=False), row['date']))
            classes, _ = read_state(conn)
        return jsonify(ok=True, classes=classes)

    @app.put('/api/settings')
    def save_settings():
        body = request.get_json()
        template = validate_template(body['template'])
        bells = body['bells']
        if set(bells) != {'1', '2'}:
            raise ValueError('Настройте звонки обеих смен.')
        for shift, values in bells.items():
            if not isinstance(values, list) or not 1 <= len(values) <= 12:
                raise ValueError('В смене должно быть от 1 до 12 уроков.')
            for v in values:
                parse_time(v)
        with db() as conn:
            for key, value in [('template', template), ('bells', bells)]:
                conn.execute('UPDATE settings SET body=? WHERE key=?', (json.dumps(value, ensure_ascii=False), key))
            conn.execute('INSERT INTO templates VALUES (?,?) ON CONFLICT(name) DO UPDATE SET body=excluded.body',
                         (template['name'], json.dumps(template, ensure_ascii=False)))
            for row in conn.execute('SELECT date,body FROM days').fetchall():
                day = json.loads(row['body'])
                day['approved'] = False
                conn.execute('UPDATE days SET body=?,revision=revision+1 WHERE date=?', (json.dumps(day, ensure_ascii=False), row['date']))
        return jsonify(ok=True)

    @app.get('/api/day/<day_date>')
    def read_day(day_date):
        checked_date(day_date)
        with db() as conn:
            body, revision = get_day(conn, day_date)
            classes, settings = read_state(conn)
            issues = check_day(conn, day_date, body, classes, settings)
            if any(i['severity'] == 'error' for i in issues):
                body['approved'] = False
        return jsonify(day=body, revision=revision, issues=issues)

    @app.put('/api/day/<day_date>')
    def save_day(day_date):
        checked_date(day_date)
        payload = request.get_json()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            classes, settings = read_state(conn)
            body = clean_day(payload['day'], classes, settings['bells'], read_teachers(conn))
            revision = write_day(conn, day_date, body, payload.get('revision'))
            sync_catalogs(conn)
            issues = check_day(conn, day_date, body, classes, settings)
        return jsonify(day=body, revision=revision, issues=issues)

    @app.get('/api/schedules')
    def saved_schedules():
        def summary(body):
            lessons = [l for l in body['lessons'] if not l.get('cancelled') and any(g.get('subject') for g in l['groups'])]
            return {'lesson_count': len(lessons), 'class_count': len({l['class_id'] for l in lessons})}

        with db() as conn:
            days = [{'date': r['date'], 'revision': r['revision'], **summary(json.loads(r['body']))}
                    for r in conn.execute('SELECT date,body,revision FROM days ORDER BY date DESC')]
            weekly = [{'weekday': r['weekday'], **summary(json.loads(r['body']))}
                      for r in conn.execute('SELECT weekday,body FROM weekly ORDER BY weekday')]
        return jsonify(days=days, weekly=weekly)

    @app.post('/api/day/<day_date>/copy')
    def copy_day(day_date):
        checked_date(day_date)
        payload = request.get_json()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if payload.get('source') not in ('date', 'saved', 'weekly'):
                raise ValueError('Выберите сохранённое расписание или недельный шаблон.')
            if payload.get('source') == 'weekly':
                weekday = payload.get('source_weekday', date.fromisoformat(day_date).weekday())
                if type(weekday) is not int or not 0 <= weekday <= 6:
                    raise ValueError('Выберите день недели для недельного шаблона.')
                row = conn.execute('SELECT body FROM weekly WHERE weekday=?', (weekday,)).fetchone()
                if not row:
                    abort(404, description='На этот день недели ещё не сохранён недельный шаблон.')
                body = resolve_teachers(json.loads(row['body']), read_teachers(conn))
            else:
                source_date = checked_date(payload.get('source_date'))
                if not conn.execute('SELECT 1 FROM days WHERE date=?', (source_date,)).fetchone():
                    abort(404, description='На исходную дату нет сохранённого расписания.')
                body, source_revision = get_day(conn, source_date)
                if payload.get('source_revision') is not None and int(payload['source_revision']) != source_revision:
                    abort(409, description='Исходное расписание изменилось. Закройте окно выбора шаблона и откройте его снова.')
            body['approved'] = False
            revision = write_day(conn, day_date, body, payload.get('revision'))
        return jsonify(ok=True, revision=revision)

    @app.post('/api/day/<day_date>/weekly')
    def save_weekly(day_date):
        checked_date(day_date)
        with db() as conn:
            body, revision = get_day(conn, day_date)
            if int(request.get_json().get('revision', revision)) != revision:
                abort(409, description='Обновите расписание перед сохранением шаблона.')
            body['approved'] = False
            conn.execute('INSERT INTO weekly VALUES (?,?) ON CONFLICT(weekday) DO UPDATE SET body=excluded.body',
                         (date.fromisoformat(day_date).weekday(), json.dumps(body, ensure_ascii=False)))
        return jsonify(ok=True)

    @app.post('/api/day/<day_date>/approve')
    def approve(day_date):
        checked_date(day_date)
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            classes, settings = read_state(conn)
            body, revision = get_day(conn, day_date)
            issues = check_day(conn, day_date, body, classes, settings)
            if any(i['severity'] == 'error' for i in issues):
                return jsonify(error='Утверждение заблокировано: исправьте критические ошибки.', issues=issues), 422
            body['approved'] = True
            revision = write_day(conn, day_date, body, request.get_json().get('revision'))
        return jsonify(ok=True, revision=revision)

    @app.get('/api/export/<day_date>/<fmt>')
    def export(day_date, fmt):
        from documents import build_document, LayoutError
        checked_date(day_date)
        if fmt not in ('pdf', 'xlsx', 'docx'):
            abort(404, description='Поддерживаются PDF, XLSX и DOCX.')
        scope = request.args.get('scope', 'both')
        if scope not in ('1', '2', 'both', 'all', 'class'):
            raise ValueError('Неизвестный набор классов.')
        with db() as conn:
            classes, settings = read_state(conn)
            body, revision = get_day(conn, day_date)
            issues = check_day(conn, day_date, body, classes, settings)
        if scope == 'class':
            cid = int(request.args.get('class_id', 0))
            selected = [c for c in classes if c['id'] == cid]
        else:
            selected = [c for c in classes if scope in ('both', 'all') or str(c['shift']) == scope]
        if not selected:
            raise ValueError('В выбранном разделе нет классов.')
        # Check the complete day, including teachers/rooms shared across shifts.
        draft = request.args.get('draft') == '1'
        if any(i['severity'] == 'error' for i in issues) and not draft:
            return jsonify(error='Экспорт официальной версии заблокирован. Исправьте ошибки или включите черновик.', issues=issues), 422
        try:
            output = build_document(fmt, day_date, body, selected, settings, draft, issues)
        except LayoutError as error:
            return jsonify(error=str(error)), 422
        filename = f'timetable_{day_date}_{scope}.{fmt}'
        mime = {'pdf': 'application/pdf', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}[fmt]
        return send_file(BytesIO(output), mimetype=mime, download_name=filename,
                         as_attachment=request.args.get('inline') != '1', max_age=0)

    from planning import install_planning
    install_planning(app, db=db, read_state=read_state, read_teachers=read_teachers, get_day=get_day,
                     week_state=week_state, write_day=write_day, check_day=check_day, checked_date=checked_date,
                     clean_teacher=clean_teacher, save_teacher_rules=save_teacher_rules, sync_teacher=sync_teacher)
    return app


def run_server():
    port = int(os.environ.get('PORT', 8765))
    lock_dir = ROOT / 'data'
    lock_dir.mkdir(exist_ok=True)
    # Windows permits multiple servers with SO_REUSEADDR on the same port.
    # Keep one instance so requests cannot alternate between old and new code.
    with (lock_dir / f'.server-{port}.lock').open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f'Application is already running: http://127.0.0.1:{port}', flush=True)
            return
        create_app().run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    run_server()
