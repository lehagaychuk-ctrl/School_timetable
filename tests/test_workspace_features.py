"""Class creation and reuse of saved schedules without losing existing data."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import unittest
from copy import deepcopy
from io import BytesIO
from openpyxl import load_workbook
from app import create_app, empty_day


class WorkspaceFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / 'school.sqlite3'
        self.client = create_app(self.database).test_client()
        self.classes = self.client.get('/api/state').json['classes']

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, value, lessons=None, note=''):
        body = empty_day()
        body['lessons'] = lessons or []
        body['notes']['1'] = note
        revision = self.client.get('/api/day/' + value).json['revision']
        response = self.client.put('/api/day/' + value, json={'day': body, 'revision': revision})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def lesson(self, cid=1, slot=1):
        return {'class_id': cid, 'slot': slot, 'groups': [{'subject': 'Математика', 'teacher': '', 'rooms': ['15']}]}

    def test_add_classes_preserves_ids_lessons_templates_and_teacher_assignments(self):
        self.save('2026-10-15', [self.lesson()], 'Примечание')
        self.client.post('/api/day/2026-10-15/weekly', json={})
        teacher = self.client.post('/api/teachers', json={'name': 'Учитель', 'subjects': ['Математика'], 'class_ids': [1]}).json['teacher']
        incoming = deepcopy(self.classes)
        incoming[0]['name'], incoming[1]['name'] = incoming[1]['name'], incoming[0]['name']
        incoming.insert(0, {'name': '  5А  ', 'shift': 2})
        incoming.append({'id': None, 'name': '6Б', 'shift': 1})
        response = self.client.put('/api/classes', json={'classes': incoming})
        self.assertEqual(response.status_code, 200, response.json)
        new_classes = response.json['classes']
        self.assertEqual(len(new_classes), 49)
        self.assertEqual(new_classes[0]['name'], '5А')
        self.assertEqual({c['id'] for c in new_classes if c['id'] <= 47}, set(range(1, 48)))
        self.assertEqual(new_classes[1]['id'], 1)
        self.assertEqual(self.client.get('/api/day/2026-10-15').json['day']['lessons'][0]['class_id'], 1)
        self.assertEqual(self.client.get('/api/state').json['teachers'][0]['class_ids'], [1])
        self.assertEqual(self.client.post('/api/day/2026-10-22/copy', json={'source': 'weekly', 'revision': 0}).status_code, 200)
        self.assertEqual(self.client.get('/api/day/2026-10-22').json['day']['notes']['1'], 'Примечание')
        new_id = new_classes[0]['id']
        teacher['class_ids'].append(new_id)
        self.assertEqual(self.client.put(f'/api/teachers/{teacher["id"]}', json=teacher).status_code, 200)
        self.assertEqual(self.client.get(f'/api/constructor/2026-10-16?class_id={new_id}&slot=1').json['class_info']['shift'], 2)
        self.save('2026-10-16', [self.lesson(new_id)])
        exported = self.client.get(f'/api/export/2026-10-16/xlsx?scope=class&class_id={new_id}')
        self.assertEqual(exported.status_code, 200)
        workbook = load_workbook(BytesIO(exported.data))
        self.assertTrue(any(c.value == '5А' for ws in workbook for row in ws for c in row))
        self.assertEqual(len(create_app(self.database).test_client().get('/api/state').json['classes']), 49)

    def test_invalid_class_additions_are_atomic_and_stale_lists_cannot_remove_classes(self):
        incoming = deepcopy(self.classes)
        incoming[0]['name'] = 'Изменение'
        incoming.append({'name': '  класс   02 ', 'shift': 1})
        self.assertEqual(self.client.put('/api/classes', json={'classes': incoming}).status_code, 400)
        self.assertEqual(self.client.get('/api/state').json['classes'], self.classes)
        incoming[-1] = {'name': 'Новый', 'shift': 3}
        self.assertEqual(self.client.put('/api/classes', json={'classes': incoming}).status_code, 400)
        self.assertEqual(self.client.get('/api/state').json['classes'], self.classes)
        incoming[-1]['shift'] = 1
        self.assertEqual(self.client.put('/api/classes', json={'classes': incoming}).status_code, 200)
        self.assertEqual(self.client.put('/api/classes', json={'classes': self.classes}).status_code, 409)
        self.assertEqual(len(self.client.get('/api/state').json['classes']), 48)
        current = self.client.get('/api/state').json['classes']
        current[1]['id'] = current[0]['id']
        self.assertEqual(self.client.put('/api/classes', json={'classes': current}).status_code, 409)

    def test_saved_schedule_catalog_counts_real_lessons_and_lists_weekly_templates(self):
        self.assertEqual(self.client.get('/api/schedules').json, {'days': [], 'weekly': []})
        lesson = self.lesson()
        lesson['groups'].append({'subject': 'Английский язык', 'teacher': '', 'rooms': ['16']})
        cancelled = self.lesson(2)
        cancelled['cancelled'] = True
        self.save('2026-10-15', [lesson, cancelled])
        self.client.post('/api/day/2026-10-15/weekly', json={})
        self.save('2026-10-16')
        catalog = self.client.get('/api/schedules').json
        self.assertEqual([item['date'] for item in catalog['days']], ['2026-10-16', '2026-10-15'])
        self.assertEqual(catalog['days'][0]['lesson_count'], 0)
        self.assertEqual(catalog['days'][1]['lesson_count'], 1)
        self.assertEqual(catalog['days'][1]['class_count'], 1)
        self.assertEqual(catalog['weekly'], [{'weekday': 3, 'lesson_count': 1, 'class_count': 1}])

    def test_saved_day_and_any_weekday_template_create_independent_unapproved_copies(self):
        self.save('2026-10-15', [self.lesson()], 'Исходное примечание')
        self.client.post('/api/day/2026-10-15/approve', json={})
        self.client.post('/api/day/2026-10-15/weekly', json={})
        source = self.client.get('/api/day/2026-10-15').json
        for target, extra in [('2026-10-16', {'source': 'saved', 'source_date': '2026-10-15', 'source_revision': source['revision']}),
                              ('2026-10-17', {'source': 'weekly', 'source_weekday': 3})]:
            self.save(target, [self.lesson(2, 2)], 'Заменить')
            revision = self.client.get('/api/day/' + target).json['revision']
            response = self.client.post(f'/api/day/{target}/copy', json={**extra, 'revision': revision})
            self.assertEqual(response.status_code, 200, response.json)
            body = self.client.get('/api/day/' + target).json['day']
            self.assertFalse(body['approved'])
            self.assertEqual(body['lessons'][0]['class_id'], 1)
            self.assertEqual(body['notes']['1'], 'Исходное примечание')
            self.save(target, [], 'Изменённая копия')
        self.assertEqual(self.client.get('/api/day/2026-10-15').json, source)
        self.assertEqual(self.client.post('/api/day/2026-10-18/copy', json={'source': 'weekly', 'source_weekday': 3, 'revision': 0}).status_code, 200)
        self.assertEqual(self.client.get('/api/day/2026-10-18').json['day']['notes']['1'], 'Исходное примечание')

    def test_template_copy_rejects_stale_source_or_target_without_overwriting(self):
        source = self.save('2026-10-15', [self.lesson()])
        target = self.save('2026-10-16', [self.lesson(2)])
        self.save('2026-10-15', [], 'Новая версия')
        payload = {'source': 'saved', 'source_date': '2026-10-15', 'source_revision': source['revision'], 'revision': target['revision']}
        self.assertEqual(self.client.post('/api/day/2026-10-16/copy', json=payload).status_code, 409)
        self.assertEqual(self.client.get('/api/day/2026-10-16').json['day'], target['day'])
        payload.pop('source_revision')
        payload['revision'] = 0
        self.assertEqual(self.client.post('/api/day/2026-10-16/copy', json=payload).status_code, 409)
        self.assertEqual(self.client.get('/api/day/2026-10-16').json['day'], target['day'])
        for extra, code in [({'source': 'weekly', 'source_weekday': 7}, 400), ({'source': 'weekly', 'source_weekday': 1}, 404), ({'source': 'bad'}, 400)]:
            self.assertEqual(self.client.post('/api/day/2026-10-16/copy', json=extra).status_code, code)


if __name__ == '__main__':
    unittest.main()
