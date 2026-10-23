"""Validation results identify exact lessons without parsing Russian messages."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import unittest
from app import create_app, empty_day


class IssueLocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.client = create_app(Path(self.temporary.name) / 'school.sqlite3').test_client()
        self.day = empty_day()
        self.day['bells'] = {'1': {'1': '08:00–08:40', '2': '08:45–09:25', '3': '09:30–10:10', '7': '13:15–13:55'},
                             '2': {'0': '13:15–13:55'}}

    def tearDown(self):
        self.temporary.cleanup()

    def lesson(self, cid, slot, **group):
        return {'class_id': cid, 'slot': slot, 'groups': [{'subject': 'Математика', 'teacher': '', 'rooms': [], **group}]}

    def issues(self):
        response = self.client.put('/api/day/2026-10-16', json={'day': self.day, 'revision': 0})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['issues']

    def test_cross_shift_conflicts_include_both_cells_and_lesson_zero(self):
        group = {'subject': 'Математика', 'teacher': 'Учитель А.А.', 'rooms': ['42']}
        self.day['lessons'] = [{'class_id': cid, 'slot': slot, 'groups': [group]} for cid, slot in [(1, 7), (25, 0)]]
        issues = self.issues()
        for label in ('Учитель', 'Кабинет'):
            issue = next(i for i in issues if i['message'].startswith(label))
            self.assertEqual(issue['cells'], [{'class_id': 1, 'slot': 7}, {'class_id': 25, 'slot': 0}])

    def test_missing_fields_point_to_the_lesson_and_empty_day_to_constructor(self):
        self.day['lessons'] = [self.lesson(1, 1)]
        issues = self.issues()
        self.assertTrue(issues)
        self.assertTrue(all(i['cells'] == [{'class_id': 1, 'slot': 1}] for i in issues))
        empty = self.client.get('/api/day/2026-10-17').json['issues']
        self.assertEqual(empty[0]['target'], 'constructor')

    def test_teacher_availability_and_load_point_to_related_current_lessons(self):
        response = self.client.post('/api/teachers', json={
            'name': 'Учитель А.А.', 'subjects': ['Математика'], 'class_ids': [1, 25],
            'weekly_hours': 0, 'daily_limit': 0, 'class_hours': {'1': 0},
            'availability_enabled': True, 'availability': {'4': ['08:00–09:00']}})
        self.assertEqual(response.status_code, 201, response.json)
        teacher = response.json['teacher']
        group = {'subject': 'Математика', 'teacher': teacher['name'], 'teacher_id': teacher['id'], 'rooms': ['42']}
        self.day['lessons'] = [{'class_id': cid, 'slot': slot, 'groups': [group]} for cid, slot in [(1, 7), (25, 0)]]
        issues = self.issues()
        expected = [{'class_id': 1, 'slot': 7}, {'class_id': 25, 'slot': 0}]
        for text in ('недельная нагрузка', 'предел на день'):
            self.assertEqual(next(i for i in issues if text in i['message'])['cells'], expected)
        self.assertEqual(next(i for i in issues if 'нагрузка класса' in i['message'])['cells'], expected[:1])
        working_hours = [i for i in issues if 'за пределами часов работы' in i['message']]
        self.assertEqual([i['cells'][0] for i in working_hours], expected)

    def test_school_rules_point_to_settings_or_only_the_affected_lessons(self):
        response = self.client.put('/api/rules', json={
            'school_days': [0], 'class_daily_limit': 1, 'subject_daily_limit': 1, 'avoid_windows': True})
        self.assertEqual(response.status_code, 200)
        self.day['lessons'] = [self.lesson(1, 1), self.lesson(1, 2),
                               {'class_id': 1, 'slot': 3, 'groups': [{'subject': 'История', 'teacher': '', 'rooms': []}]}]
        issues = self.issues()
        self.assertEqual(next(i for i in issues if 'неучебный день' in i['message'])['target'], 'rules')
        self.assertEqual(next(i for i in issues if 'предел уроков класса' in i['message'])['cells'],
                         [{'class_id': 1, 'slot': slot} for slot in (1, 2, 3)])
        self.assertEqual(next(i for i in issues if 'по одному предмету' in i['message'])['cells'],
                         [{'class_id': 1, 'slot': slot} for slot in (1, 2)])


if __name__ == '__main__':
    unittest.main()
