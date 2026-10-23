import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import json
import tempfile
import unittest
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

from app import create_app, conflicts, default_bells, empty_day
from documents import make_layout
from openpyxl import load_workbook
from docx import Document
from pypdf import PdfReader


class TimetableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'school.sqlite3'
        self.app = create_app(self.db)
        self.client = self.app.test_client()
        self.state = self.client.get('/api/state').json
        self.date = '2026-10-15'

    def tearDown(self):
        self.tmp.cleanup()

    def lesson(self, cid=1, slot=1, subject='Русский язык', teacher='Иванова А.А.', rooms=None):
        return {'class_id': cid, 'slot': slot, 'groups': [{'subject': subject, 'teacher': teacher, 'rooms': rooms or ['14'], 'label': ''}], 'cancelled': False, 'note': ''}

    def save(self, lessons, day_date=None, notes=None):
        selected = day_date or self.date
        current = self.client.get('/api/day/' + selected).json
        day = empty_day()
        day['lessons'] = lessons
        if notes:
            day['notes'] = notes
        response = self.client.put('/api/day/' + selected, json={'day': day, 'revision': current['revision']})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_initial_state_and_persistence(self):
        self.assertEqual(len(self.state['classes']), 47)
        self.assertFalse(self.client.get('/api/day/' + self.date).json['day']['lessons'])
        self.save([self.lesson()])
        restarted = create_app(self.db).test_client()
        self.assertEqual(restarted.get('/api/day/' + self.date).json['day']['lessons'][0]['groups'][0]['subject'], 'Русский язык')

    def test_daily_and_weekly_copies_are_isolated(self):
        saved = self.save([self.lesson()])
        self.assertEqual(self.client.post(f'/api/day/{self.date}/weekly', json={'revision': saved['revision']}).status_code, 200)
        self.client.post('/api/day/2026-10-16/copy', json={'source': 'date', 'source_date': self.date, 'revision': 0})
        self.save([self.lesson(subject='Математика')], '2026-10-16')
        self.assertEqual(self.client.get('/api/day/' + self.date).json['day']['lessons'][0]['groups'][0]['subject'], 'Русский язык')
        self.assertEqual(self.client.post('/api/day/2026-10-22/copy', json={'source': 'weekly', 'revision': 0}).status_code, 200)
        self.assertEqual(self.client.get('/api/day/2026-10-22').json['day']['lessons'][0]['groups'][0]['subject'], 'Русский язык')
        self.assertEqual(self.client.post('/api/day/2026-10-23/copy', json={'source': 'weekly', 'revision': 0}).status_code, 404)

    def test_conflicts_across_shifts_and_different_slots(self):
        data = self.save([self.lesson(1, 8), self.lesson(25, 1)])
        errors = [i for i in data['issues'] if i['severity'] == 'error']
        self.assertEqual(len(errors), 2)
        self.assertTrue(any('Учитель' in i['message'] for i in errors))
        self.assertTrue(any('Кабинет' in i['message'] for i in errors))

    def test_adjacent_lessons_no_conflict_and_cancelled_exempt(self):
        data = self.save([self.lesson(1, 1), self.lesson(2, 2)])
        self.assertFalse(data['issues'])

    def test_class_cannot_have_overlapping_slots_with_different_resources(self):
        settings = deepcopy(self.state['settings'])
        settings['bells']['1'][1] = '08:30–09:10'
        self.client.put('/api/settings', json=settings)
        data = self.save([self.lesson(1, 1), self.lesson(1, 2, teacher='Другой учитель', rooms=['99'])])
        self.assertTrue(any('Класс занят одновременно' in i['message'] for i in data['issues']))
        cancelled = self.lesson(2, 1)
        cancelled['cancelled'] = True
        data = self.save([self.lesson(1, 1), cancelled])
        self.assertFalse(data['issues'])

    def test_subgroups_and_replacements(self):
        lesson = self.lesson()
        lesson['groups'].append({'subject': 'Английский язык', 'teacher': 'Петрова Б.Б.', 'rooms': ['25'], 'label': 'Группа 2'})
        self.assertFalse(self.save([lesson])['issues'])
        data = self.save([lesson, self.lesson(2, 1, teacher=' петрова   б.б. ', rooms=['26'])])
        self.assertEqual(len([i for i in data['issues'] if i['severity'] == 'error']), 1)
        lesson['groups'][1]['teacher'] = 'Сидорова В.В.'
        self.assertFalse(self.save([lesson, self.lesson(2, 1, teacher='Петрова Б.Б.', rooms=['26'])])['issues'])

    def test_official_export_and_approval_blocked_draft_watermarked(self):
        self.save([self.lesson(1), self.lesson(2)])
        self.assertEqual(self.client.post(f'/api/day/{self.date}/approve', json={}).status_code, 422)
        for fmt in ('pdf', 'xlsx', 'docx'):
            self.assertEqual(self.client.get(f'/api/export/{self.date}/{fmt}?scope=class&class_id=1').status_code, 422)
            response = self.client.get(f'/api/export/{self.date}/{fmt}?scope=both&draft=1')
            self.assertEqual(response.status_code, 200)
            if fmt == 'pdf':
                text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.data)).pages)
            elif fmt == 'xlsx':
                wb = load_workbook(BytesIO(response.data))
                text = '\n'.join(str(c.value) for ws in wb for row in ws for c in row if c.value)
            else:
                doc = Document(BytesIO(response.data))
                text = '\n'.join(p.text for p in doc.paragraphs)
            self.assertIn('ЧЕРНОВИК — ЕСТЬ ОШИБКИ', text)

    def test_all_47_classes_in_actual_editable_files(self):
        lessons = [self.lesson(c['id'], 1, subject=f'Предмет {c["id"]}', teacher=f'Учитель {c["id"]}', rooms=[str(100+c['id'])]) for c in self.state['classes']]
        self.save(lessons, notes={'1': 'Групповое занятие — весь класс.', '2': 'Примечание второй смены.'})
        for fmt in ('pdf', 'xlsx', 'docx'):
            response = self.client.get(f'/api/export/{self.date}/{fmt}?scope=all')
            self.assertEqual(response.status_code, 200, response.json)
            if fmt == 'pdf':
                pdf = PdfReader(BytesIO(response.data))
                self.assertGreater(len(pdf.pages), 1)
                text = '\n'.join(p.extract_text() for p in pdf.pages)
                self.assertTrue(all(not p.images for p in pdf.pages))
            elif fmt == 'xlsx':
                wb = load_workbook(BytesIO(response.data))
                self.assertEqual(wb.sheetnames, ['1 смена', '2 смена'])
                self.assertTrue(wb.worksheets[0].merged_cells)
                text = '\n'.join(str(c.value) for ws in wb for row in ws for c in row if c.value)
                cells = [c for ws in wb for row in ws for c in row if c.value and 'Предмет ' in str(c.value)]
                self.assertTrue(all(c.alignment.wrap_text and c.border.left.style == 'thin' for c in cells))
            else:
                doc = Document(BytesIO(response.data))
                text = '\n'.join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
                with ZipFile(BytesIO(response.data)) as archive:
                    xml = archive.read('word/document.xml').decode()
                    self.assertIn('w:cantSplit', xml)
                    self.assertIn('w:tblHeader', xml)
                    self.assertFalse(any(n.startswith('word/media/') for n in archive.namelist()))
            for c in self.state['classes']:
                self.assertIn(c['name'], text)
                self.assertIn(f'Предмет {c["id"]} {100+c["id"]}', text)
            self.assertIn('Примечание второй смены.', text)
            self.assertIn('15.10.2026', text)

    def test_class_scope_and_formula_injection_is_plain_text(self):
        self.save([self.lesson(subject='=1+2'), self.lesson(25, subject='НЕ ВЫВОДИТЬ', teacher='Другой учитель', rooms=['99'])])
        response = self.client.get(f'/api/export/{self.date}/xlsx?scope=class&class_id=1')
        wb = load_workbook(BytesIO(response.data))
        self.assertEqual(len(wb.sheetnames), 1)
        cells = [c for row in wb.active for c in row if c.value]
        self.assertTrue(any(str(c.value).startswith('=1+2') and c.data_type == 's' for c in cells))
        self.assertFalse(any('НЕ ВЫВОДИТЬ' in str(c.value) for c in cells))

    def test_version_conflict_does_not_overwrite(self):
        self.save([self.lesson()])
        response = self.client.put('/api/day/' + self.date, json={'day': empty_day(), 'revision': 0})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.client.get('/api/day/' + self.date).json['day']['lessons']), 1)

    def test_approval_invalidated_and_class_order_saved(self):
        data = self.save([self.lesson()])
        self.assertEqual(self.client.post(f'/api/day/{self.date}/approve', json={'revision': data['revision']}).status_code, 200)
        self.assertTrue(self.client.get('/api/day/' + self.date).json['day']['approved'])
        classes = deepcopy(self.state['classes'])
        classes.reverse()
        classes[0]['name'] = '11А'
        self.assertEqual(self.client.put('/api/classes', json={'classes': classes}).status_code, 200)
        self.assertEqual(self.client.get('/api/state').json['classes'][0]['name'], '11А')
        self.assertFalse(self.client.get('/api/day/' + self.date).json['day']['approved'])

    def test_invalid_settings_transaction_and_template_persistence(self):
        settings = deepcopy(self.state['settings'])
        settings['bells']['1'] = ['99:00–99:40']
        self.assertEqual(self.client.put('/api/settings', json=settings).status_code, 400)
        self.assertEqual(self.client.get('/api/state').json['settings'], self.state['settings'])
        settings = deepcopy(self.state['settings'])
        settings['template'].update(name='Свой шаблон', paper='A3', orientation='portrait', show_teachers=True)
        self.assertEqual(self.client.put('/api/settings', json=settings).status_code, 200)
        self.assertIn('Свой шаблон', [t['name'] for t in self.client.get('/api/state').json['templates']])

    def test_shrinking_bells_never_loses_saved_lessons(self):
        self.save([self.lesson(1, 8, subject='СОХРАНЁННЫЙ УРОК')])
        settings = deepcopy(self.state['settings'])
        settings['bells']['1'] = settings['bells']['1'][:3]
        self.client.put('/api/settings', json=settings)
        self.assertEqual(self.client.get(f'/api/export/{self.date}/pdf').status_code, 422)
        pdf = self.client.get(f'/api/export/{self.date}/pdf?draft=1')
        text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(pdf.data)).pages)
        self.assertIn('СОХРАНЁННЫЙ УРОК', text)
        self.assertIn('Время не задано', ' '.join(text.split()))

    def test_measured_pagination_long_groups_and_notes(self):
        settings = deepcopy(self.state['settings'])
        settings['template'].update(orientation='portrait', font_size=12, classes_per_table=8, show_teachers=True, between_notes='Примечание между блоками.')
        self.client.put('/api/settings', json=settings)
        lessons = []
        for c in self.state['classes']:
            for slot in range(1, 9):
                lesson = self.lesson(c['id'], slot, subject='Дополнительное занятие по английскому языку и литературе', teacher=f'Учитель {c["id"]}', rooms=[str(100+c['id'])])
                lesson['groups'].append({'subject':'Иностранный язык — вторая подгруппа', 'teacher':f'Преподаватель {c["id"]}', 'rooms':[str(200+c['id'])], 'label':'Группа 2'})
                lessons.append(lesson)
        saved = self.save(lessons, notes={'1': 'Общее примечание.\n' * 80, '2': ''})
        pages = make_layout(self.date, saved['day'], self.state['classes'], settings, False, saved['issues'])
        self.assertGreater(len(pages), 8)
        for page in pages:
            self.assertGreaterEqual(page['remaining'], 0)
            for item in page['items']:
                if item['type'] == 'table':
                    self.assertEqual(item['rows'][0][0], '№')
                    self.assertGreater(len(item['rows']), 1)
        pdf = self.client.get(f'/api/export/{self.date}/pdf')
        self.assertEqual(pdf.status_code, 200)
        text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(pdf.data)).pages)
        for c in self.state['classes']:
            self.assertIn(c['name'], text)

    def test_local_write_origin_guard_and_validation(self):
        self.assertEqual(self.client.put('/api/classes', json={'classes': []}, headers={'Origin':'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/day/not-a-date').status_code, 400)
        self.assertEqual(self.client.put('/api/classes', json={'classes': []}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
