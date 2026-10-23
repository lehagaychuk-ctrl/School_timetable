"""Teacher availability, workload and eligibility for the timetable constructor."""
from collections import Counter
from datetime import date, timedelta
import re


def normalized(value):
    return ' '.join(str(value).split()).casefold()


def parse_time(value):
    match = re.fullmatch(r'(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})', value.strip())
    if not match:
        raise ValueError('Время укажите в формате 08:00–08:40.')
    h1, m1, h2, m2 = map(int, match.groups())
    if max(h1, h2) > 23 or max(m1, m2) > 59 or h1 * 60 + m1 >= h2 * 60 + m2:
        raise ValueError('Начало урока должно предшествовать окончанию в пределах одного дня.')
    return h1 * 60 + m1, h2 * 60 + m2


def week_dates(day_date):
    day = date.fromisoformat(day_date)
    monday = day - timedelta(days=day.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def day_bells(day, bells, shift):
    """Numbered bells for this date; legacy global lists start at lesson one."""
    override = day.get('bells', {}).get(str(shift))
    if override is not None:
        return {int(slot): value for slot, value in sorted(override.items(), key=lambda pair: int(pair[0]))}
    return dict(enumerate(bells[str(shift)], 1))


def teaching_entries(day, classes, bells):
    class_map = {c['id']: c for c in classes}
    for lesson in day['lessons']:
        cls = class_map.get(lesson['class_id'])
        if lesson.get('cancelled') or not cls:
            continue
        time = day_bells(day, bells, cls['shift']).get(lesson['slot'])
        interval = parse_time(time) if time else None
        for index, group in enumerate(lesson['groups']):
            if group.get('subject'):
                yield {'teacher_id': group.get('teacher_id'), 'teacher': group.get('teacher', ''),
                       'class_id': cls['id'], 'class_name': cls['name'], 'slot': lesson['slot'],
                       'group_index': index, 'subject': group['subject'], 'rooms': group.get('rooms', []),
                       'shift': cls['shift'], 'time': time or '',
                       'group': group.get('label') or (f'Группа {index + 1}' if len(lesson['groups']) > 1 else ''),
                       'interval': interval}


def usage_for_week(days, classes, bells):
    totals, by_class, daily = Counter(), Counter(), Counter()
    for day_date, body in days.items():
        for entry in teaching_entries(body, classes, bells):
            tid = entry['teacher_id']
            if tid:
                totals[tid] += 1
                by_class[(tid, entry['class_id'])] += 1
                daily[(tid, day_date)] += 1
    return totals, by_class, daily


def candidate(teacher, day_date, cls, slot, day, classes, bells, week):
    reasons = []
    if not teacher['active']:
        reasons.append('Учитель находится в архиве.')
    if not teacher['subjects']:
        reasons.append('В карточке учителя не указаны предметы.')
    if cls['id'] not in teacher.get('class_ids', []):
        reasons.append('Этот класс не указан в карточке учителя.')
    time = day_bells(day, bells, cls['shift']).get(slot)
    if not time:
        reasons.append('Для урока не задано время звонка.')
        interval = None
    else:
        interval = parse_time(time)
    if interval and teacher.get('availability_enabled'):
        windows = teacher.get('availability', {}).get(str(date.fromisoformat(day_date).weekday()), [])
        if not any(start <= interval[0] and interval[1] <= end for start, end in map(parse_time, windows)):
            reasons.append('Урок выходит за часы работы учителя в этот день недели.')
    entries = list(teaching_entries(day, classes, bells))
    busy_lessons = []
    if interval:
        for entry in entries:
            other = entry['interval']
            if entry['teacher_id'] == teacher['id'] and other and interval[0] < other[1] and other[0] < interval[1]:
                reasons.append(f'Учитель занят: {entry["class_name"]}, урок {entry["slot"]}.')
                busy_lessons.append({key: entry[key] for key in ('class_id', 'class_name', 'slot', 'subject', 'rooms', 'shift', 'time', 'group')})
    totals, by_class, daily = usage_for_week(week, classes, bells)
    total, class_total, day_total = totals[teacher['id']], by_class[(teacher['id'], cls['id'])], daily[(teacher['id'], day_date)]
    weekly = teacher.get('weekly_hours')
    class_target = teacher.get('class_hours', {}).get(str(cls['id']))
    daily_limit = teacher.get('daily_limit')
    if weekly is not None and total >= weekly:
        reasons.append(f'Недельная нагрузка выполнена: {total} из {weekly} уроков.')
    if class_target is not None and class_total >= class_target:
        reasons.append(f'Нагрузка в этом классе выполнена: {class_total} из {class_target} уроков за неделю.')
    if daily_limit is not None and day_total >= daily_limit:
        reasons.append(f'Достигнут предел на день: {day_total} из {daily_limit} уроков.')
    busy_rooms = set()
    if interval:
        for entry in entries:
            other = entry['interval']
            if other and interval[0] < other[1] and other[0] < interval[1]:
                busy_rooms.update(normalized(r) for r in entry['rooms'])
    return {'teacher': teacher, 'available': not reasons, 'reasons': list(dict.fromkeys(reasons)),
            'busy_lessons': busy_lessons,
            'week_count': total, 'class_week_count': class_total, 'day_count': day_total,
            'class_target': class_target, 'free_rooms': [r for r in teacher['rooms'] if normalized(r) not in busy_rooms],
            'busy_rooms': [r for r in teacher['rooms'] if normalized(r) in busy_rooms]}


def teacher_rule_issues(day_date, day, teachers, classes, bells, week):
    issues, teacher_map = [], {t['id']: t for t in teachers}
    totals, by_class, daily = usage_for_week(week, classes, bells)
    used = set()
    entries = list(teaching_entries(day, classes, bells))

    def cells_for(tid, cid=None):
        return [{'class_id': class_id, 'slot': slot} for class_id, slot in dict.fromkeys(
            (e['class_id'], e['slot']) for e in entries
            if e['teacher_id'] == tid and (cid is None or e['class_id'] == cid))]

    for entry in entries:
        teacher = teacher_map.get(entry['teacher_id'])
        if not teacher:
            continue  # Preserve legacy free-text lessons; the constructor requires catalog IDs.
        used.add(teacher['id'])
        prefix = f'{entry["class_name"]}, урок {entry["slot"]}: {teacher["name"]}'
        cells = [{'class_id': entry['class_id'], 'slot': entry['slot']}]
        if teacher.get('class_ids') and entry['class_id'] not in teacher['class_ids']:
            issues.append({'severity': 'error', 'message': prefix + ' не назначен этому классу.', 'cells': cells})
        if teacher.get('availability_enabled') and entry['interval']:
            windows = teacher['availability'].get(str(date.fromisoformat(day_date).weekday()), [])
            if not any(start <= entry['interval'][0] and entry['interval'][1] <= end for start, end in map(parse_time, windows)):
                issues.append({'severity': 'error', 'message': prefix + ' — занятие за пределами часов работы.', 'cells': cells})
    for tid in used:
        teacher = teacher_map[tid]
        weekly = teacher.get('weekly_hours')
        daily_limit = teacher.get('daily_limit')
        if weekly is not None and totals[tid] > weekly:
            issues.append({'severity': 'error', 'message': f'{teacher["name"]}: превышена недельная нагрузка — {totals[tid]} из {weekly} уроков.', 'cells': cells_for(tid)})
        if daily_limit is not None and daily[(tid, day_date)] > daily_limit:
            issues.append({'severity': 'error', 'message': f'{teacher["name"]}: превышен предел на день — {daily[(tid, day_date)]} из {daily_limit} уроков.', 'cells': cells_for(tid)})
        for cid, target in teacher.get('class_hours', {}).items():
            if by_class[(tid, int(cid))] > target:
                class_name = next((c['name'] for c in classes if c['id'] == int(cid)), cid)
                issues.append({'severity': 'error', 'message': f'{teacher["name"]}, {class_name}: превышена нагрузка класса — {by_class[(tid, int(cid))]} из {target} уроков за неделю.',
                               'cells': cells_for(tid, int(cid)), 'target': 'teachers'})
    return issues
