import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import unittest
from app import create_app, empty_day


class ConstructorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(Path(self.tmp.name) / 'builder.sqlite3').test_client()
        self.day = '2026-10-15'  # Thursday

    def tearDown(self):
        self.tmp.cleanup()

    def teacher(self, **changes):
        value = {'name': 'Учитель А.А.', 'subjects': ['Математика'], 'rooms': ['14'], 'class_ids': [1, 2, 25],
                 'availability_enabled': True, 'availability': {str(i): ['08:00–18:00'] for i in range(5)},
                 'weekly_hours': 18, 'daily_limit': 6, 'class_hours': {'1': 5}}
        value.update(changes)
        result = self.client.post('/api/teachers', json=value)
        self.assertEqual(result.status_code, 201, result.json)
        return result.json['teacher']

    def context(self, cid=1, slot=1, day=None):
        result = self.client.get(f'/api/constructor/{day or self.day}?class_id={cid}&slot={slot}')
        self.assertEqual(result.status_code, 200, result.json)
        return result.json

    def assign(self, teacher, cid=1, slot=1, day=None, **changes):
        value = {'teacher_id': teacher['id'], 'class_id': cid, 'slot': slot, 'subject': 'Математика',
                 'rooms': ['14'], 'revision': self.context(cid, slot, day)['revision']}
        value.update(changes)
        return self.client.post(f'/api/constructor/{day or self.day}', json=value)

    def save_raw(self, teacher, lessons, day=None):
        body = empty_day()
        body['lessons'] = [{'class_id': cid, 'slot': slot, 'cancelled': cancelled,
                            'groups': [{'teacher_id': teacher['id'], 'teacher': teacher['name'], 'subject': 'Математика', 'rooms': [str(100+cid)]}]} for cid, slot, cancelled in lessons]
        selected = day or self.day
        revision = self.client.get('/api/day/' + selected).json['revision']
        result = self.client.put('/api/day/' + selected, json={'day': body, 'revision': revision})
        self.assertEqual(result.status_code, 200, result.json)
        return result.json

    def test_assignment_class_and_entire_working_window(self):
        teacher = self.teacher(class_ids=[1], availability={'3': ['08:00–08:40']})
        self.assertTrue(self.context()['candidates'][0]['available'])
        self.assertFalse(self.context(2)['candidates'][0]['available'])
        self.assertEqual(self.assign(teacher, cid=2).status_code, 422)
        self.assertFalse(self.context(slot=2)['candidates'][0]['available'])
        self.assertFalse(self.context(day='2026-10-16')['candidates'][0]['available'])
        result = self.assign(teacher)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['day']['lessons'][0]['groups'][0]['teacher_id'], teacher['id'])

    def test_breaks_and_adjacent_work_windows(self):
        self.teacher(availability={'3': ['08:00–09:00', '09:10–12:00']})
        self.assertFalse(self.context(slot=2)['candidates'][0]['available'])
        other = self.teacher(name='Второй учитель', availability={'3': ['08:00–09:00', '09:00–12:00']})
        self.assertEqual(other['availability']['3'], ['08:00–12:00'])
        choices = self.context(slot=2)['candidates']
        self.assertTrue(next(c for c in choices if c['teacher']['id'] == other['id'])['available'])

    def test_real_overlapping_intervals_across_shifts(self):
        teacher = self.teacher()
        self.save_raw(teacher, [(1, 8, False)])
        choice = self.context(25, 1)['candidates'][0]
        self.assertFalse(choice['available'])
        self.assertTrue(any('занят' in r for r in choice['reasons']))
        self.assertEqual(choice['busy_lessons'], [{'class_id': 1, 'class_name': 'Класс 01', 'slot': 8,
            'subject': 'Математика', 'rooms': ['101'], 'shift': 1, 'time': '14:05–14:45', 'group': ''}])
        self.assertEqual(self.context(25, 1)['busy_teachers'][0]['teacher']['id'], teacher['id'])
        self.assertEqual(self.assign(teacher, 25, 1).status_code, 422)

    def test_busy_tab_excludes_cancelled_and_nonoverlapping_lessons_keeps_archived(self):
        teacher = self.teacher()
        self.save_raw(teacher, [(1, 8, False), (2, 1, True)])
        self.assertFalse(self.context(2, 2)['busy_teachers'])
        self.assertFalse(self.context(2, 1)['busy_teachers'])
        self.client.put(f'/api/teachers/{teacher["id"]}', json={**teacher, 'active': False})
        context = self.context(25, 1)
        self.assertFalse(context['candidates'])
        self.assertEqual(len(context['busy_teachers']), 1)
        self.assertFalse(context['busy_teachers'][0]['teacher']['active'])
        self.assertEqual(context['busy_teachers'][0]['busy_lessons'][0]['class_id'], 1)

    def test_weekly_class_and_daily_limits_cancelled_lessons(self):
        teacher = self.teacher(weekly_hours=2, daily_limit=1, class_hours={'1': 1})
        self.assertEqual(self.assign(teacher).status_code, 200)
        self.assertFalse(self.context(1, 2)['candidates'][0]['available'])
        self.assertFalse(self.context(2, 2)['candidates'][0]['available'])
        self.assertFalse(self.context(1, 1, '2026-10-16')['candidates'][0]['available'])
        self.assertEqual(self.assign(teacher, 2, 1, '2026-10-16', rooms=['15']).status_code, 200)
        self.assertFalse(self.context(25, 1, '2026-10-17')['candidates'][0]['available'])
        self.assertTrue(self.context(1, 1, '2026-10-22')['candidates'][0]['available'])
        self.save_raw(teacher, [(1, 1, True)])
        context = self.context(1, 2)['candidates'][0]
        self.assertEqual(context['week_count'], 1)
        self.assertEqual(context['class_week_count'], 0)

    def test_weekly_templates_count_and_saved_dates_override(self):
        teacher = self.teacher(weekly_hours=2, class_hours={})
        saved = self.save_raw(teacher, [(1, 1, False)])
        self.client.post(f'/api/day/{self.day}/weekly', json={'revision': saved['revision']})
        context = self.context(2, 1, '2026-10-23')
        self.assertEqual(context['candidates'][0]['week_count'], 1)
        self.assertEqual(context['week_sources']['2026-10-22'], 'weekly')
        self.save_raw(teacher, [], '2026-10-22')
        self.assertEqual(self.context(2, 1, '2026-10-23')['candidates'][0]['week_count'], 0)

    def test_room_conflict_subject_and_stale_revision(self):
        first = self.teacher(name='Первый', class_hours={})
        second = self.teacher(name='Второй', class_hours={})
        self.assertEqual(self.assign(first).status_code, 200)
        self.assertEqual(self.assign(second, cid=2).status_code, 422)
        self.assertEqual(self.assign(second, cid=2, rooms=['15'], subject='Физика').status_code, 400)
        self.assertEqual(self.assign(second, cid=2, rooms=['15'], revision=0).status_code, 409)
        self.assertEqual(self.assign(second, cid=2, rooms=['15']).status_code, 200)
        self.assertEqual(self.assign(second, cid=2, rooms=['15']).status_code, 409)

    def test_rule_errors_block_approval_and_exports_even_after_manual_edit(self):
        teacher = self.teacher(class_ids=[1], availability={'3': ['10:00–12:00']}, weekly_hours=1, class_hours={'1': 1})
        result = self.save_raw(teacher, [(2, 1, False), (1, 2, False)])
        messages = ' '.join(i['message'] for i in result['issues'])
        self.assertIn('не назначен', messages)
        self.assertIn('часов работы', messages)
        self.assertIn('недельная нагрузка', messages)
        self.assertEqual(self.client.post(f'/api/day/{self.day}/approve', json={}).status_code, 422)
        for fmt in ('pdf', 'xlsx', 'docx'):
            self.assertEqual(self.client.get(f'/api/export/{self.day}/{fmt}').status_code, 422)
            self.assertEqual(self.client.get(f'/api/export/{self.day}/{fmt}?draft=1').status_code, 200)

    def test_validation_and_rules_change_invalidates_approval(self):
        for changes in [{'class_ids': [999]}, {'weekly_hours': -1}, {'weekly_hours': 1.5},
                        {'daily_limit': 13}, {'class_ids': [1], 'class_hours': {'2': 3}},
                        {'availability': {'3': ['08:00–10:00', '09:00–11:00']}}]:
            value = {'name': 'Учитель', 'class_ids': [1], **changes}
            self.assertEqual(self.client.post('/api/teachers', json=value).status_code, 400)
        teacher = self.teacher()
        saved = self.assign(teacher).json
        self.client.post(f'/api/day/{self.day}/approve', json={'revision': saved['revision']})
        self.client.put(f'/api/teachers/{teacher["id"]}', json={**teacher, 'availability': {'3': ['14:00–18:00']}})
        day = self.client.get('/api/day/' + self.day).json
        self.assertFalse(day['day']['approved'])
        self.assertGreater(day['revision'], saved['revision'])
        self.assertTrue(any('часов работы' in i['message'] for i in day['issues']))

    def test_other_date_workload_change_blocks_previously_approved_day(self):
        teacher = self.teacher(weekly_hours=1, class_hours={})
        saved = self.assign(teacher).json
        self.assertEqual(self.client.post(f'/api/day/{self.day}/approve', json={'revision': saved['revision']}).status_code, 200)
        self.save_raw(teacher, [(2, 1, False)], day='2026-10-16')
        previous = self.client.get('/api/day/' + self.day).json
        self.assertFalse(previous['day']['approved'])
        self.assertTrue(any('недельная нагрузка' in i['message'] for i in previous['issues']))
        self.assertEqual(self.client.get(f'/api/export/{self.day}/pdf').status_code, 422)


if __name__ == '__main__':
    unittest.main()
