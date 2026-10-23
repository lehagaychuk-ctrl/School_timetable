"""Accounts, school isolation, curriculum, safe planning and atomic imports."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import json
import sqlite3
import tempfile
import unittest
from app import create_app, empty_day


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'school.sqlite3'
        self.app = create_app(self.path)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, client=None, email='first@school.example'):
        client = client or self.client
        response = client.post('/api/account/register', json={'school': 'Школа', 'name': 'Пользователь', 'email': email, 'password': 'School-Password-2026!'})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_first_account_claims_existing_school_then_login_is_required(self):
        before = self.client.get('/api/state').json['classes']
        value = self.register()
        self.assertTrue(value['existing_school'])
        self.assertEqual(self.client.get('/api/state').json['classes'], before)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/api/state').status_code, 401)
        self.assertEqual(anonymous.get('/api/backup').status_code, 401)
        self.assertIn('login-form', anonymous.get('/').get_data(as_text=True))
        self.assertEqual(anonymous.post('/api/account/login', json={'email': value['user']['email'], 'password': 'wrong'}).status_code, 401)
        self.assertEqual(anonymous.post('/api/account/login', json={'email': value['user']['email'], 'password': 'School-Password-2026!'}).status_code, 200)
        self.assertEqual(anonymous.get('/api/state').json['classes'], before)
        self.assertEqual(anonymous.post('/api/account/logout', json={}).status_code, 200)
        self.assertEqual(anonymous.get('/api/state').status_code, 401)

    def test_new_school_is_empty_and_cannot_access_another_school(self):
        self.register()
        second = self.app.test_client()
        value = self.register(second, 'second@school.example')
        self.assertFalse(value['existing_school'])
        self.assertEqual(second.get('/api/state').json['classes'], [])
        self.assertEqual(second.get('/api/state').json['teachers'], [])
        self.assertEqual(second.put('/api/classes', json={'classes': [{'name': 'Новый класс', 'shift': 1}]}).status_code, 200)
        self.assertEqual(len(second.get('/api/state').json['classes']), 1)
        self.assertEqual(len(self.client.get('/api/state').json['classes']), 47)
        self.assertFalse(self.client.get('/api/state').json['teachers'])

    def test_password_and_recovery_are_hashed_and_recovery_revokes_sessions(self):
        value = self.register()
        with sqlite3.connect(self.path.with_name('school.accounts.sqlite3')) as connection:
            hashes = connection.execute('SELECT password_hash,recovery_hash FROM accounts').fetchone()
        connection.close()
        self.assertNotIn('School-Password-2026!', hashes)
        self.assertNotIn(value['recovery_code'], hashes)
        other = self.app.test_client()
        replacement = other.post('/api/account/reset', json={'email': value['user']['email'], 'password': 'Changed-Password-2026!', 'recovery_code': value['recovery_code']})
        self.assertEqual(replacement.status_code, 200, replacement.json)
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(other.post('/api/account/reset', json={'email': value['user']['email'], 'password': 'Changed-Password-2026!', 'recovery_code': value['recovery_code']}).status_code, 401)
        self.assertEqual(other.post('/api/account/login', json={'email': value['user']['email'], 'password': 'School-Password-2026!'}).status_code, 401)
        restarted = create_app(self.path).test_client()
        self.assertEqual(restarted.post('/api/account/login', json={'email': value['user']['email'], 'password': 'Changed-Password-2026!'}).status_code, 200)

    def test_validation_duplicate_and_cross_origin_registration(self):
        body = {'school': 'Школа', 'name': 'Имя', 'email': 'user@school.example', 'password': 'short'}
        self.assertEqual(self.client.post('/api/account/register', json=body).status_code, 400)
        body['password'] = 'School-Password-2026!'
        self.assertEqual(self.client.post('/api/account/register', json=body, headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/account/register', json=body).status_code, 201)
        body['email'] = 'USER@SCHOOL.EXAMPLE'
        self.assertEqual(self.client.post('/api/account/register', json=body).status_code, 409)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(Path(self.tmp.name) / 'school.sqlite3').test_client()
        response = self.client.post('/api/teachers', json={'name': 'Учитель', 'subjects': ['Математика'], 'rooms': ['101'], 'class_ids': [1],
                                                         'weekly_hours': 3, 'class_hours': {'1': 3}, 'daily_limit': 1,
                                                         'availability_enabled': True, 'availability': {'0': ['08:00–09:30'], '2': ['08:00–09:30'], '4': ['08:00–09:30']}})
        self.assertEqual(response.status_code, 201, response.json)
        self.teacher = response.json['teacher']
        data = self.client.get('/api/planning?date=2026-10-19').json
        self.subject = next(c for c in data['catalog'] if c['kind'] == 'subject')
        self.room = next(c for c in data['catalog'] if c['kind'] == 'room')
        self.course = {'class_id': 1, 'subject_id': self.subject['id'], 'teacher_id': self.teacher['id'], 'weekly_hours': 3, 'room_ids': [self.room['id']]}
        self.assertEqual(self.client.post('/api/curriculum', json=self.course).status_code, 200)
        self.rules = {'school_days': [0, 1, 2, 3, 4], 'class_daily_limit': 2, 'subject_daily_limit': 1, 'avoid_windows': True}
        self.assertEqual(self.client.put('/api/rules', json=self.rules).status_code, 200)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, value, lessons):
        body = empty_day()
        body['lessons'] = lessons
        response = self.client.put('/api/day/' + value, json={'day': body})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def lesson(self, cid=1, slot=1, teacher=True):
        return {'class_id': cid, 'slot': slot, 'groups': [{'subject': 'Математика', 'teacher_id': self.teacher['id'] if teacher else None,
                                                        'teacher': self.teacher['name'] if teacher else 'Другой', 'rooms': ['101']}]}

    def preview(self):
        response = self.client.post('/api/autofill/preview', json={'date': '2026-10-19'})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_autofill_preview_is_readonly_and_apply_respects_constraints_and_existing_lessons(self):
        monday = self.save('2026-10-19', [self.lesson()])
        busy = self.save('2026-10-21', [self.lesson(2, teacher=False)])
        value = self.preview()
        self.assertEqual(value['added'], 2)
        self.assertEqual(value['unplaced_hours'], 0)
        self.assertEqual(self.client.get('/api/day/2026-10-19').json, monday)
        self.assertEqual(self.client.get('/api/day/2026-10-21').json, busy)
        self.assertEqual({p['date'] for p in value['proposed']}, {'2026-10-21', '2026-10-23'})
        self.assertEqual(next(p['slot'] for p in value['proposed'] if p['date'] == '2026-10-21'), 2)
        self.assertEqual(self.client.post('/api/autofill/apply', json={'run_id': value['run_id']}).status_code, 200)
        self.assertEqual(len(self.client.get('/api/day/2026-10-21').json['day']['lessons']), 2)
        self.assertEqual(self.client.post('/api/autofill/apply', json={'run_id': value['run_id']}).status_code, 409)
        data = self.client.get('/api/planning?date=2026-10-19').json
        self.assertEqual(data['curriculum'][0]['remaining_hours'], 0)
        self.assertFalse(self.client.get('/api/day/2026-10-23').json['issues'])

    def test_autofill_stale_preview_does_not_overwrite_new_data(self):
        value = self.preview()
        changed = self.save('2026-10-19', [self.lesson(2, teacher=False)])
        self.assertEqual(self.client.post('/api/autofill/apply', json={'run_id': value['run_id']}).status_code, 409)
        self.assertEqual(self.client.get('/api/day/2026-10-19').json, changed)
        self.assertFalse(self.client.get('/api/day/2026-10-21').json['day']['lessons'])

    def test_unplaced_load_has_reasons_and_does_not_bypass_teacher_limits(self):
        teacher = {**self.teacher, 'weekly_hours': 1}
        self.assertEqual(self.client.put('/api/teachers/' + str(teacher['id']), json=teacher).status_code, 200)
        value = self.preview()
        self.assertEqual(value['added'], 1)
        self.assertEqual(value['unplaced_hours'], 2)
        self.assertTrue(any('Недельная нагрузка' in reason for reason in value['unplaced'][0]['reasons']))

    def test_resource_views_include_class_time_and_weekly_template_sources(self):
        self.save('2026-10-19', [self.lesson()])
        self.client.post('/api/day/2026-10-19/weekly', json={})
        for kind, ident in [('teacher', self.teacher['id']), ('room', self.room['id'])]:
            value = self.client.get(f'/api/resource-schedule/2026-10-26?kind={kind}&id={ident}&week=1').json
            monday = value['days'][0]
            self.assertEqual(monday['source'], 'weekly')
            self.assertEqual(monday['lessons'][0]['class_name'], 'Класс 01')
            self.assertEqual(monday['lessons'][0]['time'], '08:00–08:40')

    def test_catalog_rename_propagates_and_stale_catalog_edits_are_rejected(self):
        self.save('2026-10-19', [self.lesson()])
        changed = {**self.room, 'name': '202'}
        self.assertEqual(self.client.put('/api/catalog/' + str(self.room['id']), json=changed).status_code, 200)
        self.assertEqual(self.client.get('/api/day/2026-10-19').json['day']['lessons'][0]['groups'][0]['rooms'], ['202'])
        self.assertEqual(self.client.get('/api/state').json['teachers'][0]['rooms'], ['202'])
        self.assertEqual(self.client.get('/api/planning?date=2026-10-19').json['curriculum'][0]['rooms'], ['202'])
        self.assertEqual(self.client.put('/api/catalog/' + str(self.room['id']), json=changed).status_code, 409)

    def test_curriculum_requires_assigned_class_subject_and_valid_room(self):
        self.assertEqual(self.client.post('/api/curriculum', json={**self.course, 'class_id': 2}).status_code, 400)
        self.assertEqual(self.client.post('/api/curriculum', json={**self.course, 'room_ids': [9999]}).status_code, 400)
        self.assertEqual(self.client.post('/api/curriculum', json=self.course).status_code, 409)

    def test_school_rules_apply_to_manual_lessons_approval_and_export(self):
        saved = self.save('2026-10-25', [self.lesson()])
        self.assertTrue(any('неучебный день' in issue['message'] for issue in saved['issues']))
        self.assertEqual(self.client.post('/api/day/2026-10-25/approve', json={}).status_code, 422)
        self.assertEqual(self.client.get('/api/export/2026-10-25/pdf?scope=class&class_id=1').status_code, 422)
        self.assertEqual(self.client.get('/api/export/2026-10-25/pdf?scope=class&class_id=1&draft=1').status_code, 200)

    def test_import_preview_and_atomic_apply_skip_existing_names(self):
        text = 'name;shift;print_group\nКласс 01;1;\nНовый класс;2;Новая таблица\n'
        response = self.client.post('/api/import/preview', json={'kind': 'classes', 'format': 'csv', 'text': text})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['added'], 1)
        self.assertEqual(response.json['skipped'], ['Класс 01'])
        self.assertEqual(len(self.client.get('/api/state').json['classes']), 47)
        self.assertEqual(self.client.post('/api/import/apply', json={'run_id': response.json['run_id']}).status_code, 200)
        self.assertEqual(len(self.client.get('/api/state').json['classes']), 48)
        self.assertEqual(self.client.post('/api/import/apply', json={'run_id': response.json['run_id']}).status_code, 409)
        invalid = self.client.post('/api/import/preview', json={'kind': 'classes', 'format': 'csv', 'text': 'name;shift\nЕщё класс;1\nОшибка;5\n'})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(len(self.client.get('/api/state').json['classes']), 48)

    def test_teacher_import_and_backup_exclude_account_credentials(self):
        row = {'name': 'Новый учитель', 'subjects': ['Физика'], 'rooms': ['12'], 'classes': ['Класс 02'], 'weekly_hours': 2}
        response = self.client.post('/api/import/preview', json={'kind': 'teachers', 'format': 'json', 'text': json.dumps([row])})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.client.post('/api/import/apply', json={'run_id': response.json['run_id']}).status_code, 200)
        value = self.client.get('/api/backup').json
        self.assertEqual(len(value['teachers']), 2)
        self.assertNotIn('accounts', value)
        self.assertNotIn('password', json.dumps(value))
        teacher = next(t for t in value['teachers'] if t['name'] == row['name'])
        self.assertEqual(teacher['class_ids'], [2])


if __name__ == '__main__':
    unittest.main()
