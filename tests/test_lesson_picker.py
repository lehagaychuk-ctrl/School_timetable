"""Checked assignments can replace one group without double-counting its lesson."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import unittest
from app import create_app, empty_day


class LessonPickerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.client = create_app(Path(self.temporary.name) / 'school.sqlite3').test_client()
        self.date = '2026-10-15'

    def tearDown(self):
        self.temporary.cleanup()

    def teacher(self, name, **changes):
        value = {'name': name, 'subjects': ['Математика', 'Алгебра'], 'class_ids': [1, 2], 'rooms': ['14'], **changes}
        response = self.client.post('/api/teachers', json=value)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['teacher']

    def group(self, teacher, **changes):
        return {'teacher_id': teacher['id'], 'teacher': teacher['name'], 'subject': 'Математика', 'rooms': teacher['rooms'], 'label': '', **changes}

    def save(self, lessons):
        day = empty_day()
        day['lessons'] = lessons
        revision = self.current()['revision']
        response = self.client.put('/api/day/' + self.date, json={'day': day, 'revision': revision})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def current(self):
        return self.client.get('/api/day/' + self.date).json

    def context(self, index=0):
        return self.client.get(f'/api/constructor/{self.date}?class_id=1&slot=1&group_index={index}')

    def assign(self, teacher, **changes):
        return self.client.post('/api/constructor/' + self.date, json={
            'class_id': 1, 'slot': 1, 'group_index': 0, 'teacher_id': teacher['id'],
            'subject': 'Алгебра', 'rooms': teacher['rooms'], 'revision': self.current()['revision'], **changes})

    def test_current_group_does_not_block_its_own_teacher_at_exact_load_limits(self):
        teacher = self.teacher('Первый', weekly_hours=1, daily_limit=1, class_hours={'1': 1})
        self.save([{'class_id': 1, 'slot': 1, 'groups': [self.group(teacher)], 'note': 'Сохранить примечание'}])
        choice = self.context().json['candidates'][0]
        self.assertTrue(choice['available'], choice['reasons'])
        self.assertEqual((choice['week_count'], choice['day_count'], choice['class_week_count']), (0, 0, 0))
        response = self.assign(teacher)
        self.assertEqual(response.status_code, 200, response.json)
        lesson = response.json['day']['lessons'][0]
        self.assertEqual(lesson['note'], 'Сохранить примечание')
        self.assertEqual(lesson['groups'][0]['subject'], 'Алгебра')

    def test_replacement_preserves_other_groups_labels_and_notes(self):
        first, second, third = self.teacher('Первый'), self.teacher('Второй', rooms=['15']), self.teacher('Третий', rooms=['16'])
        saved = self.save([{'class_id': 1, 'slot': 1, 'groups': [self.group(first, label='Группа А'), self.group(second, label='Группа Б')], 'note': 'Примечание'}])
        choices = self.context().json['candidates']
        self.assertFalse(next(c for c in choices if c['teacher']['id'] == second['id'])['available'])
        response = self.assign(third)
        self.assertEqual(response.status_code, 200, response.json)
        lesson = response.json['day']['lessons'][0]
        self.assertEqual(lesson['groups'][0]['label'], 'Группа А')
        self.assertEqual(lesson['groups'][1], saved['day']['lessons'][0]['groups'][1])
        self.assertEqual(lesson['note'], 'Примечание')

    def test_other_class_busy_teacher_and_eligibility_remain_enforced(self):
        teacher = self.teacher('Первый')
        self.save([{'class_id': 2, 'slot': 1, 'groups': [self.group(teacher)]}])
        self.assertFalse(self.context().json['candidates'][0]['available'])
        before = self.current()
        self.assertEqual(self.assign(teacher).status_code, 422)
        self.assertEqual(self.current(), before)
        other = self.teacher('Другой класс', class_ids=[2])
        self.assertEqual(self.assign(other).status_code, 422)

    def test_revision_group_index_and_subject_are_checked_without_writing(self):
        teacher = self.teacher('Первый')
        self.save([{'class_id': 1, 'slot': 1, 'groups': [self.group(teacher)]}])
        before = self.current()
        self.assertEqual(self.assign(teacher, revision=0).status_code, 409)
        self.assertEqual(self.assign(teacher, subject='История').status_code, 400)
        for index in (-1, True, 0.5):
            self.assertEqual(self.assign(teacher, group_index=index).status_code, 400)
        self.assertEqual(self.assign(teacher, group_index=1).status_code, 409)
        self.assertEqual(self.current(), before)

    def test_room_conflict_rechecked_when_replacing(self):
        first, second = self.teacher('Первый'), self.teacher('Второй', rooms=['15'])
        self.save([{'class_id': 1, 'slot': 1, 'groups': [self.group(first)]},
                   {'class_id': 2, 'slot': 1, 'groups': [self.group(second)]}])
        before = self.current()
        response = self.assign(first, rooms=['15'])
        self.assertEqual(response.status_code, 422, response.json)
        self.assertTrue(any('Кабинет' in issue['message'] for issue in response.json['issues']))
        self.assertEqual(self.current(), before)

    def test_reactivating_cancelled_group_cannot_conflict_in_another_group(self):
        first, second, third = self.teacher('Первый'), self.teacher('Второй', rooms=['15']), self.teacher('Третий', rooms=['16'])
        self.save([{'class_id': 1, 'slot': 1, 'groups': [self.group(first), self.group(second)], 'cancelled': True},
                   {'class_id': 2, 'slot': 1, 'groups': [self.group(second)]}])
        before = self.current()
        self.assertEqual(self.assign(third).status_code, 422)
        self.assertEqual(self.current(), before)


if __name__ == '__main__':
    unittest.main()
