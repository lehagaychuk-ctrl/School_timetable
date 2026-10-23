"""School catalogs, curriculum, resource views, previewed weekly planning and import."""
from collections import Counter
from copy import deepcopy
from datetime import date
from io import StringIO, BytesIO
import csv
import hashlib
import json
import secrets
import time

from flask import abort, jsonify, request, send_file
from scheduling import normalized, parse_time, week_dates, teaching_entries, candidate, day_bells

DEFAULT_RULES = {'school_days': list(range(7)), 'class_daily_limit': None,
                 'subject_daily_limit': None, 'avoid_windows': True}


def initialize_planning(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS catalog_items(id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL CHECK(kind IN ('subject','room')),name TEXT NOT NULL,name_key TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',capacity INTEGER,active INTEGER NOT NULL DEFAULT 1,
          revision INTEGER NOT NULL DEFAULT 1,UNIQUE(kind,name_key));
        CREATE TABLE IF NOT EXISTS curriculum(id INTEGER PRIMARY KEY AUTOINCREMENT,
          class_id INTEGER NOT NULL REFERENCES classes(id), subject_id INTEGER NOT NULL REFERENCES catalog_items(id),
          teacher_id INTEGER NOT NULL REFERENCES teachers(id),weekly_hours INTEGER NOT NULL,
          room_ids TEXT NOT NULL DEFAULT '[]',revision INTEGER NOT NULL DEFAULT 1,
          UNIQUE(class_id,subject_id,teacher_id));
        CREATE TABLE IF NOT EXISTS planning_runs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,
          body TEXT NOT NULL,created REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
    ''')
    sync_catalogs(conn)


def sync_catalogs(conn):
    values = {'subject': set(), 'room': set()}
    for teacher in conn.execute('SELECT subjects,rooms FROM teachers'):
        values['subject'].update(json.loads(teacher['subjects']))
        values['room'].update(json.loads(teacher['rooms']))
    for table in ('days', 'weekly'):
        for row in conn.execute('SELECT body FROM ' + table):
            for lesson in json.loads(row['body'])['lessons']:
                for group in lesson['groups']:
                    if group.get('subject'):
                        values['subject'].add(group['subject'])
                    values['room'].update(group.get('rooms', []))
    for kind, names in values.items():
        for name in sorted(names):
            if str(name).strip():
                conn.execute('INSERT OR IGNORE INTO catalog_items(kind,name,name_key) VALUES (?,?,?)',
                             (kind, str(name).strip(), normalized(name)))


def rules_value(value):
    if not isinstance(value, dict):
        raise ValueError('Передайте настройки правил.')
    result = {**DEFAULT_RULES, **{k: v for k, v in value.items() if k in DEFAULT_RULES}}
    days = result['school_days']
    if not isinstance(days, list) or not days or any(type(i) is not int or not 0 <= i <= 6 for i in days):
        raise ValueError('Выберите хотя бы один учебный день недели.')
    result['school_days'] = sorted(set(days))
    for key in ('class_daily_limit', 'subject_daily_limit'):
        if result[key] in ('', None):
            result[key] = None
        elif type(result[key]) is not int or not 1 <= result[key] <= 12:
            raise ValueError('Предел уроков должен быть целым числом от 1 до 12 или пустым.')
    if type(result['avoid_windows']) is not bool:
        raise ValueError('Некорректная настройка уменьшения окон.')
    return result


def planning_rule_issues(day_date, day, classes, settings):
    rules = rules_value(settings.get('planning_rules', DEFAULT_RULES))
    active = [l for l in day['lessons'] if not l.get('cancelled') and any(g.get('subject') for g in l['groups'])]
    issues = []
    if active and date.fromisoformat(day_date).weekday() not in rules['school_days']:
        issues.append({'severity': 'error', 'message': 'На эту дату по правилам школы задан неучебный день.', 'target': 'rules'})
    for cls in classes:
        lessons = [l for l in active if l['class_id'] == cls['id']]
        if rules['class_daily_limit'] and len(lessons) > rules['class_daily_limit']:
            issues.append({'severity': 'error', 'message': f'{cls["name"]}: превышен предел уроков класса на день.',
                           'cells': [{'class_id': cls['id'], 'slot': l['slot']} for l in lessons]})
        counts = Counter(normalized(g['subject']) for l in lessons for g in l['groups'] if g.get('subject'))
        if rules['subject_daily_limit'] and any(n > rules['subject_daily_limit'] for n in counts.values()):
            issues.append({'severity': 'error', 'message': f'{cls["name"]}: превышен дневной предел занятий по одному предмету.',
                           'cells': [{'class_id': cls['id'], 'slot': l['slot']} for l in lessons
                                     if any(counts[normalized(g['subject'])] > rules['subject_daily_limit'] for g in l['groups'] if g.get('subject'))]})
    return issues


def catalog_rows(conn):
    return [{**dict(r), 'active': bool(r['active'])} for r in conn.execute('SELECT * FROM catalog_items ORDER BY kind,name_key,id')]


def fingerprint(conn):
    value = {}
    for table in ('classes', 'teachers', 'catalog_items', 'curriculum', 'settings', 'days', 'weekly'):
        value[table] = [tuple(r) for r in conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def install_planning(app, *, db, read_state, read_teachers, get_day, week_state, write_day,
                     check_day, checked_date, clean_teacher, save_teacher_rules, sync_teacher):
    def courses(conn):
        result = []
        classes, _ = read_state(conn)
        class_map = {c['id']: c for c in classes}
        teachers = {t['id']: t for t in read_teachers(conn)}
        catalog = {c['id']: c for c in catalog_rows(conn)}
        for row in conn.execute('SELECT * FROM curriculum ORDER BY class_id,subject_id,id'):
            item = dict(row)
            item['room_ids'] = json.loads(item['room_ids'])
            item.update(class_name=class_map[item['class_id']]['name'],
                        teacher_name=teachers[item['teacher_id']]['name'],
                        subject=catalog[item['subject_id']]['name'],
                        rooms=[catalog[r]['name'] for r in item['room_ids']])
            result.append(item)
        return result

    def clean_course(conn, body):
        value = {key: int(body[key]) for key in ('class_id', 'subject_id', 'teacher_id', 'weekly_hours')}
        classes, _ = read_state(conn)
        teacher = next((t for t in read_teachers(conn) if t['id'] == value['teacher_id'] and t['active']), None)
        subject = conn.execute("SELECT * FROM catalog_items WHERE id=? AND kind='subject' AND active=1", (value['subject_id'],)).fetchone()
        if value['class_id'] not in {c['id'] for c in classes} or not teacher or not subject:
            raise ValueError('Выберите существующие класс, предмет и действующего учителя.')
        if value['class_id'] not in teacher['class_ids']:
            raise ValueError('Сначала отметьте этот класс в карточке учителя.')
        if normalized(subject['name']) not in {normalized(s) for s in teacher['subjects']}:
            raise ValueError('Сначала добавьте этот предмет в карточку учителя.')
        if not 1 <= value['weekly_hours'] <= 84:
            raise ValueError('Нагрузка по предмету — от 1 до 84 уроков в неделю.')
        rooms = body.get('room_ids', [])
        if not isinstance(rooms, list) or len(rooms) > 4 or any(type(r) is not int for r in rooms):
            raise ValueError('Выберите до четырёх кабинетов из справочника.')
        known = {r['id'] for r in catalog_rows(conn) if r['kind'] == 'room' and r['active']}
        if not set(rooms).issubset(known):
            raise ValueError('Выбран неизвестный или архивный кабинет.')
        value['room_ids'] = list(dict.fromkeys(rooms))
        return value

    def write_course(conn, value, course_id=None):
        duplicate = conn.execute('SELECT id FROM curriculum WHERE class_id=? AND subject_id=? AND teacher_id=?',
                                 (value['class_id'], value['subject_id'], value['teacher_id'])).fetchone()
        if duplicate and duplicate['id'] != course_id:
            abort(409, description='Эта строка учебной программы уже существует.')
        params = (value['class_id'], value['subject_id'], value['teacher_id'], value['weekly_hours'], json.dumps(value['room_ids']))
        if course_id:
            conn.execute('UPDATE curriculum SET class_id=?,subject_id=?,teacher_id=?,weekly_hours=?,room_ids=?,revision=revision+1 WHERE id=?', (*params, course_id))
        else:
            conn.execute('INSERT INTO curriculum(class_id,subject_id,teacher_id,weekly_hours,room_ids) VALUES (?,?,?,?,?)', params)

    def invalidate(conn):
        for row in conn.execute('SELECT date,body FROM days').fetchall():
            body = json.loads(row['body'])
            body['approved'] = False
            conn.execute('UPDATE days SET body=?,revision=revision+1 WHERE date=?', (json.dumps(body, ensure_ascii=False), row['date']))

    @app.get('/api/planning')
    def planning_state():
        selected = checked_date(request.args.get('date'))
        with db() as conn:
            classes, settings = read_state(conn)
            body, _ = get_day(conn, selected)
            week, sources = week_state(conn, selected)
            program = courses(conn)
            placed = sum(1 for day in week.values() for l in day['lessons'] if not l.get('cancelled') and any(g.get('subject') for g in l['groups']))
            remaining = 0
            for course in program:
                count = sum(1 for day in week.values() for e in teaching_entries(day, classes, settings['bells'])
                            if e['class_id'] == course['class_id'] and e['teacher_id'] == course['teacher_id'] and normalized(e['subject']) == normalized(course['subject']))
                course['placed_hours'] = count
                course['remaining_hours'] = max(0, course['weekly_hours'] - count)
                remaining += course['remaining_hours']
            issues = check_day(conn, selected, body, classes, settings)
            return jsonify(catalog=catalog_rows(conn), curriculum=program,
                           rules=rules_value(settings.get('planning_rules', DEFAULT_RULES)),
                           week_start=week_dates(selected)[0], week_sources=sources,
                           metrics={'classes': len(classes), 'teachers': sum(t['active'] for t in read_teachers(conn)),
                                    'rooms': sum(c['kind'] == 'room' and c['active'] for c in catalog_rows(conn)),
                                    'subjects': sum(c['kind'] == 'subject' and c['active'] for c in catalog_rows(conn)),
                                    'hours': sum(c['weekly_hours'] for c in program), 'placed': placed,
                                    'unplaced': remaining, 'violations': sum(i['severity'] == 'error' for i in issues)})

    @app.post('/api/catalog')
    @app.put('/api/catalog/<int:item_id>')
    def save_catalog(item_id=None):
        body = request.get_json()
        kind, name = body.get('kind'), ' '.join(str(body.get('name', '')).split())
        if kind not in ('room', 'subject') or not 1 <= len(name) <= (40 if kind == 'room' else 120):
            raise ValueError('Выберите тип и заполните название кабинета (до 40 символов) или предмета (до 120).')
        note, active, capacity = str(body.get('note', '')).strip(), body.get('active', True), body.get('capacity')
        if len(note) > 1000 or type(active) is not bool or (capacity is not None and (type(capacity) is not int or not 1 <= capacity <= 1000)):
            raise ValueError('Примечание — до 1000 символов, вместимость — от 1 до 1000 или пустая.')
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM catalog_items WHERE id=?', (item_id,)).fetchone() if item_id else None
            if item_id and (not old or old['kind'] != kind):
                abort(404, description='Запись справочника не найдена.')
            if old and old['revision'] != body.get('revision'):
                abort(409, description='Запись изменилась. Обновите справочник.')
            other = conn.execute('SELECT id FROM catalog_items WHERE kind=? AND name_key=?', (kind, normalized(name))).fetchone()
            if other and other['id'] != item_id:
                abort(409, description='Такое название уже есть в справочнике, включая архив.')
            if old:
                conn.execute('UPDATE catalog_items SET name=?,name_key=?,note=?,capacity=?,active=?,revision=revision+1 WHERE id=?',
                             (name, normalized(name), note, capacity, int(active), item_id))
                if old['name'] != name:
                    for table, primary in [('days', 'date'), ('weekly', 'weekday')]:
                        for row in conn.execute(f'SELECT {primary},body FROM {table}').fetchall():
                            day, changed = json.loads(row['body']), False
                            for lesson in day['lessons']:
                                for group in lesson['groups']:
                                    if kind == 'subject' and normalized(group.get('subject', '')) == old['name_key']:
                                        group['subject'], changed = name, True
                                    if kind == 'room' and any(normalized(r) == old['name_key'] for r in group['rooms']):
                                        group['rooms'] = [name if normalized(r) == old['name_key'] else r for r in group['rooms']]
                                        changed = True
                            if changed:
                                day['approved'] = False
                                extra = ',revision=revision+1' if table == 'days' else ''
                                conn.execute(f'UPDATE {table} SET body=?{extra} WHERE {primary}=?', (json.dumps(day, ensure_ascii=False), row[primary]))
                    column = 'subjects' if kind == 'subject' else 'rooms'
                    for teacher in read_teachers(conn):
                        if any(normalized(v) == old['name_key'] for v in teacher[column]):
                            values = [name if normalized(v) == old['name_key'] else v for v in teacher[column]]
                            conn.execute(f'UPDATE teachers SET {column}=?,revision=revision+1 WHERE id=?', (json.dumps(values, ensure_ascii=False), teacher['id']))
            else:
                conn.execute('INSERT INTO catalog_items(kind,name,name_key,note,capacity,active) VALUES (?,?,?,?,?,?)',
                             (kind, name, normalized(name), note, capacity, int(active)))
        return jsonify(ok=True)

    @app.post('/api/curriculum')
    @app.put('/api/curriculum/<int:course_id>')
    def save_course(course_id=None):
        body = request.get_json()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if course_id:
                old = conn.execute('SELECT * FROM curriculum WHERE id=?', (course_id,)).fetchone()
                if not old:
                    abort(404, description='Строка программы не найдена.')
                if old['revision'] != body.get('revision'):
                    abort(409, description='Учебная программа изменилась. Обновите страницу.')
            write_course(conn, clean_course(conn, body), course_id)
        return jsonify(ok=True)

    @app.delete('/api/curriculum/<int:course_id>')
    def delete_course(course_id):
        with db() as conn:
            row = conn.execute('SELECT * FROM curriculum WHERE id=?', (course_id,)).fetchone()
            if not row:
                abort(404, description='Строка программы не найдена.')
            if request.get_json().get('revision') != row['revision']:
                abort(409, description='Учебная программа изменилась. Обновите страницу.')
            conn.execute('DELETE FROM curriculum WHERE id=?', (course_id,))
        return jsonify(ok=True)

    @app.put('/api/rules')
    def save_rules():
        value = rules_value(request.get_json())
        with db() as conn:
            conn.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body', ('planning_rules', json.dumps(value)))
            invalidate(conn)
        return jsonify(ok=True)

    @app.get('/api/resource-schedule/<day_date>')
    def resource_schedule(day_date):
        checked_date(day_date)
        kind, resource_id = request.args.get('kind'), int(request.args.get('id', 0))
        with db() as conn:
            classes, settings = read_state(conn)
            if kind == 'teacher':
                resource = next((t for t in read_teachers(conn) if t['id'] == resource_id), None)
            elif kind == 'room':
                resource = next((r for r in catalog_rows(conn) if r['id'] == resource_id and r['kind'] == 'room'), None)
            else:
                raise ValueError('Выберите расписание учителя или кабинета.')
            if not resource:
                abort(404, description='Учитель или кабинет не найден.')
            body, _ = get_day(conn, day_date)
            days, sources = week_state(conn, day_date) if request.args.get('week') == '1' else ({day_date: body}, {day_date: 'day'})
            result = []
            for value, day in days.items():
                entries = [e for e in teaching_entries(day, classes, settings['bells']) if (e['teacher_id'] == resource_id if kind == 'teacher' else any(normalized(r) == normalized(resource['name']) for r in e['rooms']))]
                result.append({'date': value, 'source': sources[value], 'lessons': sorted(entries, key=lambda e: (e['interval'] or (9999, 9999), e['class_name']))})
        return jsonify(resource={'id': resource_id, 'name': resource['name']}, days=result)

    def store_run(conn, kind, value):
        run_id = secrets.token_urlsafe(24)
        value['fingerprint'] = fingerprint(conn)
        conn.execute('DELETE FROM planning_runs WHERE created<?', (time.time() - 3600,))
        conn.execute('INSERT INTO planning_runs(id,kind,body,created) VALUES (?,?,?,?)', (run_id, kind, json.dumps(value, ensure_ascii=False), time.time()))
        return run_id

    def load_run(conn, run_id, kind):
        row = conn.execute('SELECT * FROM planning_runs WHERE id=? AND kind=?', (run_id, kind)).fetchone()
        if not row or row['used'] or row['created'] < time.time() - 3600:
            abort(409, description='Предпросмотр устарел или уже применён. Сформируйте его заново.')
        value = json.loads(row['body'])
        if value['fingerprint'] != fingerprint(conn):
            abort(409, description='Данные школы изменились после предпросмотра. Сформируйте его заново.')
        return value

    @app.post('/api/autofill/preview')
    def autofill_preview():
        selected = checked_date(request.get_json().get('date'))
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            classes, settings = read_state(conn)
            rules = rules_value(settings.get('planning_rules', DEFAULT_RULES))
            teachers = {t['id']: t for t in read_teachers(conn)}
            class_map = {c['id']: c for c in classes}
            items = {c['id']: c for c in catalog_rows(conn)}
            program = courses(conn)
            if not program:
                raise ValueError('Сначала заполните учебную программу: класс, предмет, учитель и часы в неделю.')
            if sum(c['weekly_hours'] for c in program) > 4000:
                raise ValueError('Для одного подбора поддерживается до 4000 часов учебной программы в неделю.')
            days, sources = week_state(conn, selected)
            proposed, unplaced = [], []
            for course in sorted(program, key=lambda c: (not teachers[c['teacher_id']]['availability_enabled'], -c['weekly_hours'], c['id'])):
                cls, teacher = class_map[course['class_id']], teachers[course['teacher_id']]
                already = sum(1 for body in days.values() for e in teaching_entries(body, classes, settings['bells']) if e['class_id'] == cls['id'] and e['teacher_id'] == teacher['id'] and normalized(e['subject']) == normalized(course['subject']))
                remaining = max(0, course['weekly_hours'] - already)
                reasons = Counter()
                for _ in range(remaining):
                    options = []
                    for value, body in days.items():
                        if date.fromisoformat(value).weekday() not in rules['school_days']:
                            continue
                        existing = [l for l in body['lessons'] if l['class_id'] == cls['id']]
                        active = [l for l in existing if not l.get('cancelled')]
                        same = sum(any(normalized(g['subject']) == normalized(course['subject']) for g in l['groups']) for l in active)
                        if rules['class_daily_limit'] and len(active) >= rules['class_daily_limit']:
                            reasons['Достигнут дневной предел уроков класса.'] += 1
                            continue
                        if rules['subject_daily_limit'] and same >= rules['subject_daily_limit']:
                            reasons['Достигнут дневной предел этого предмета.'] += 1
                            continue
                        for slot, bell in day_bells(body, settings['bells'], cls['shift']).items():
                            if any(l['slot'] == slot for l in existing):
                                continue
                            choice = candidate(teacher, value, cls, slot, body, classes, settings['bells'], days)
                            if not choice['available']:
                                reasons.update(choice['reasons'])
                                continue
                            if not items[course['subject_id']]['active']:
                                reasons['Предмет находится в архиве.'] += 1
                                continue
                            start, end = parse_time(bell)
                            busy = {normalized(r) for e in teaching_entries(body, classes, settings['bells']) if e['interval'] and start < e['interval'][1] and e['interval'][0] < end for r in e['rooms']}
                            # A class cannot overlap itself even if custom bells overlap.
                            if any(e['class_id'] == cls['id'] and e['interval'] and start < e['interval'][1] and e['interval'][0] < end for e in teaching_entries(body, classes, settings['bells'])):
                                continue
                            if course['room_ids']:
                                rooms = [items[r]['name'] for r in course['room_ids']]
                                room_ok = all(items[r]['active'] for r in course['room_ids']) and all(normalized(r) not in busy for r in rooms)
                            else:
                                available_rooms = [r['name'] for r in items.values() if r['kind'] == 'room' and r['active'] and normalized(r['name']) not in busy]
                                preferred = [r for r in teacher['rooms'] if normalized(r) in {normalized(v) for v in available_rooms}]
                                rooms = (preferred or available_rooms)[:1]
                                room_ok = bool(rooms)
                            if not room_ok:
                                reasons['Нет свободного подходящего кабинета.'] += 1
                                continue
                            slots = [l['slot'] for l in active]
                            gaps = max(max(slots + [slot]) - min(slots + [slot]) + 1 - len(slots) - 1, 0)
                            score = len(active) * 15 + same * 30 + slot + (gaps * 40 if rules['avoid_windows'] else 0)
                            options.append((score, value, slot, rooms, bell))
                    if not options:
                        unplaced.append({**course, 'hours': remaining - sum(1 for p in proposed if p['course_id'] == course['id']),
                                         'reasons': [r for r, _ in reasons.most_common(4)] or ['Нет свободных ячеек в учебные дни.']})
                        break
                    _, value, slot, rooms, bell = min(options)
                    days[value]['lessons'].append({'class_id': cls['id'], 'slot': slot, 'groups': [{'subject': course['subject'], 'teacher_id': teacher['id'], 'teacher': teacher['name'], 'rooms': rooms, 'label': ''}], 'cancelled': False, 'note': ''})
                    days[value]['approved'] = False
                    proposed.append({'course_id': course['id'], 'date': value, 'class_name': cls['name'], 'slot': slot,
                                     'time': bell, 'subject': course['subject'], 'teacher': teacher['name'], 'rooms': rooms})
            changed_dates = {p['date'] for p in proposed}
            run_id = store_run(conn, 'autofill', {'days': {d: days[d] for d in changed_dates}, 'proposed': proposed,
                                                 'revisions': {d: get_day(conn, d)[1] for d in changed_dates}})
        return jsonify(run_id=run_id, week_start=week_dates(selected)[0], proposed=proposed, unplaced=unplaced,
                       added=len(proposed), unplaced_hours=sum(c['hours'] for c in unplaced), sources=sources)

    @app.post('/api/autofill/apply')
    def autofill_apply():
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value = load_run(conn, request.get_json().get('run_id'), 'autofill')
            classes, settings = read_state(conn)
            old_errors = {d: {i['message'] for i in check_day(conn, d, get_day(conn, d)[0], classes, settings) if i['severity'] == 'error'} for d in value['days']}
            for d, body in value['days'].items():
                write_day(conn, d, body, value['revisions'][d])
            for d, body in value['days'].items():
                new_errors = [i for i in check_day(conn, d, body, classes, settings) if i['severity'] == 'error' and i['message'] not in old_errors[d]]
                if new_errors:
                    abort(422, description='Подбор не применён: возникли новые конфликты. Измените программу или правила.')
            conn.execute('UPDATE planning_runs SET used=1 WHERE id=?', (request.get_json()['run_id'],))
        return jsonify(ok=True, added=len(value['proposed']))

    @app.get('/api/backup')
    def download_backup():
        with db() as conn:
            classes, settings = read_state(conn)
            value = {'format': 'school-timetable-1', 'classes': classes, 'teachers': read_teachers(conn),
                     'settings': settings, 'catalog': catalog_rows(conn), 'curriculum': courses(conn),
                     'days': [dict(r) | {'body': json.loads(r['body'])} for r in conn.execute('SELECT * FROM days ORDER BY date')],
                     'weekly': [dict(r) | {'body': json.loads(r['body'])} for r in conn.execute('SELECT * FROM weekly ORDER BY weekday')]}
        return send_file(BytesIO(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')), mimetype='application/json', download_name='school-backup.json', as_attachment=True)

    IMPORT_FIELDS = {'classes': ['name', 'shift', 'print_group'], 'teachers': ['name', 'subjects', 'rooms', 'classes', 'weekly_hours'],
                     'curriculum': ['class', 'subject', 'teacher', 'weekly_hours', 'rooms']}

    @app.get('/api/import/template')
    def import_template():
        kind = request.args.get('kind')
        if kind not in IMPORT_FIELDS:
            raise ValueError('Выберите классы, учителей или учебную программу.')
        output = StringIO()
        csv.writer(output, delimiter=';').writerow(IMPORT_FIELDS[kind])
        return send_file(BytesIO(output.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f'{kind}-template.csv')

    def import_rows(conn, kind, records):
        if kind not in IMPORT_FIELDS or not isinstance(records, list) or not 1 <= len(records) <= 1000:
            raise ValueError('Импортируйте от 1 до 1000 строк классов, учителей или учебной программы.')
        classes, _ = read_state(conn)
        class_map = {normalized(c['name']): c for c in classes}
        teachers = {normalized(t['name']): t for t in read_teachers(conn)}
        catalog = catalog_rows(conn)
        subjects = {normalized(c['name']): c for c in catalog if c['kind'] == 'subject' and c['active']}
        rooms = {normalized(c['name']): c for c in catalog if c['kind'] == 'room' and c['active']}
        cleaned, skipped, seen = [], [], set()
        def split(value):
            return value if isinstance(value, list) else [v.strip() for v in str(value or '').replace(';', ',').split(',') if v.strip()]
        for index, row in enumerate(records, 2):
            if not isinstance(row, dict):
                raise ValueError(f'Строка {index}: ожидается объект с именами полей.')
            try:
                if kind == 'classes':
                    name = ' '.join(str(row.get('name', '')).split())
                    shift, group = int(row.get('shift', 1)), str(row.get('print_group', '')).strip()
                    if not 1 <= len(name) <= 40 or shift not in (1, 2) or len(group) > 60:
                        raise ValueError('Проверьте название, смену 1/2 и группу печати.')
                    if normalized(name) in class_map or normalized(name) in seen:
                        skipped.append(name)
                        continue
                    value = {'name': name, 'shift': shift, 'print_group': group}
                    seen.add(normalized(name))
                elif kind == 'teachers':
                    body = {**row, 'subjects': split(row.get('subjects')), 'rooms': split(row.get('rooms'))}
                    body['class_ids'] = [class_map[normalized(v)]['id'] for v in split(row.get('classes'))] if 'class_ids' not in row else row['class_ids']
                    value = clean_teacher(body, classes)
                    if normalized(value['name']) in teachers or normalized(value['name']) in seen:
                        skipped.append(value['name'])
                        continue
                    seen.add(normalized(value['name']))
                else:
                    cls, teacher, subject = class_map[normalized(row['class'])], teachers[normalized(row['teacher'])], subjects[normalized(row['subject'])]
                    value = clean_course(conn, {'class_id': cls['id'], 'teacher_id': teacher['id'], 'subject_id': subject['id'], 'weekly_hours': row['weekly_hours'],
                                               'room_ids': [rooms[normalized(r)]['id'] for r in split(row.get('rooms'))]})
                    key = (value['class_id'], value['teacher_id'], value['subject_id'])
                    if key in seen or conn.execute('SELECT 1 FROM curriculum WHERE class_id=? AND teacher_id=? AND subject_id=?', key).fetchone():
                        skipped.append(f'{cls["name"]}: {subject["name"]}')
                        continue
                    seen.add(key)
                cleaned.append(value)
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f'Строка {index}: {error}. Проверьте названия и заполнение справочников.') from error
        return cleaned, skipped

    @app.post('/api/import/preview')
    def preview_import():
        body = request.get_json()
        text = str(body.get('text', '')).lstrip('\ufeff')
        if len(text) > 2_000_000:
            raise ValueError('Файл импорта должен быть меньше 2 МБ.')
        if body.get('format') == 'json':
            try:
                records = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError('Некорректный JSON: ' + error.msg) from error
        else:
            try:
                dialect = csv.Sniffer().sniff(text[:10000], delimiters=';,\t')
                records = list(csv.DictReader(StringIO(text), dialect=dialect))
            except csv.Error as error:
                raise ValueError('Не удалось прочитать CSV. Скачайте шаблон с названиями столбцов.') from error
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            cleaned, skipped = import_rows(conn, body.get('kind'), records)
            run_id = store_run(conn, 'import', {'kind': body['kind'], 'records': cleaned})
        return jsonify(run_id=run_id, rows=cleaned, skipped=skipped, added=len(cleaned))

    @app.post('/api/import/apply')
    def apply_import():
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            value = load_run(conn, request.get_json().get('run_id'), 'import')
            for item in value['records']:
                if value['kind'] == 'classes':
                    position = conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM classes').fetchone()[0]
                    conn.execute('INSERT INTO classes(name,shift,position,print_group) VALUES (?,?,?,?)', (item['name'], item['shift'], position, item['print_group']))
                elif value['kind'] == 'teachers':
                    cursor = conn.execute('INSERT INTO teachers(name,name_key,subjects,rooms,note,active) VALUES (?,?,?,?,?,?)', (item['name'], normalized(item['name']), json.dumps(item['subjects'], ensure_ascii=False), json.dumps(item['rooms'], ensure_ascii=False), item['note'], int(item['active'])))
                    save_teacher_rules(conn, cursor.lastrowid, item)
                    teacher = next(t for t in read_teachers(conn) if t['id'] == cursor.lastrowid)
                    sync_teacher(conn, teacher)
                else:
                    write_course(conn, item)
            sync_catalogs(conn)
            conn.execute('UPDATE planning_runs SET used=1 WHERE id=?', (request.get_json()['run_id'],))
        return jsonify(ok=True, added=len(value['records']))
