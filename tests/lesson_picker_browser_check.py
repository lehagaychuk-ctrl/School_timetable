"""Choose teachers and subjects in the timetable, including groups and stale data."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor'), str(ROOT / 'tests')]

import tempfile
import threading
from app import create_app, empty_day
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'school.sqlite3')
        client = app.test_client()

        def teacher(name, subjects, **changes):
            response = client.post('/api/teachers', json={'name': name, 'subjects': subjects, 'class_ids': [1], 'rooms': ['17'], **changes})
            assert response.status_code == 201, response.json
            return response.json['teacher']

        first = teacher('Иванова Анна Александровна', ['Математика', 'Алгебра'], weekly_hours=1, daily_limit=1, class_hours={'1': 1})
        second = teacher('Петров Пётр Петрович', ['Физика', 'География'], rooms=['18'], class_ids=[1, 25])
        busy = teacher('Сидорова — занята в другом классе', ['Английский язык'], rooms=['19'], class_ids=[1, 2])
        other = teacher('Учитель только другого класса', ['Химия'], rooms=['20'], class_ids=[2])
        outside = teacher('Учитель после десяти', ['История'], availability_enabled=True, availability={'3': ['10:00–12:00']})
        full = teacher('Учитель без свободной нагрузки', ['Биология'], weekly_hours=0)
        day = empty_day()
        day['bells'] = {'1': {'1': '08:00–08:40', '2': '08:55–09:35'},
                        '2': {'0': '13:15–13:55', '1': '14:00–14:40'}}
        day['lessons'] = [{'class_id': 2, 'slot': 1, 'groups': [{'teacher_id': busy['id'], 'teacher': busy['name'], 'subject': 'Английский язык', 'rooms': ['19']}]}]
        assert client.put('/api/day/2026-10-15', json={'day': day, 'revision': 0}).status_code == 200
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/?date=2026-10-15#schedule')
                expect(page.locator('#day-date')).to_have_value('2026-10-15')
                cell = page.locator('#grid-wrap [data-class="1"][data-slot="1"]')
                cell.click()
                expect(page.locator('#lesson-picker-dialog')).to_be_visible()
                expect(page.locator('#schedule')).to_be_visible()
                expect(page.locator('#constructor')).not_to_be_visible()
                expect(cell).to_have_class('cell-button empty is-editing')
                expect(page.locator('[data-picker-card]')).to_have_count(2)
                expect(page.locator(f'[data-picker-card="{other["id"]}"]')).to_have_count(0)
                page.locator('#lesson-picker-unavailable > summary').click()
                for person in (busy, outside, full):
                    expect(page.locator(f'[data-picker-unavailable="{person["id"]}"]')).to_be_visible()
                expect(page.locator(f'[data-picker-unavailable="{busy["id"]}"]')).to_contain_text('Класс 02, урок 1')
                subject = page.locator(f'[data-picker-card="{first["id"]}"]').get_by_role('button', name='Математика', exact=True)
                subject.focus()
                subject.press('Enter')
                expect(page.locator('#lesson-picker-rooms')).to_have_value('17')
                expect(page.locator('#lesson-picker-save')).to_be_enabled()
                page.screenshot(path=str(ROOT / 'test-output/cell-picker-desktop.png'))
                page.locator('#lesson-picker-save').click()
                expect(page.locator('#lesson-picker-dialog')).not_to_be_visible()
                expect(cell).to_contain_text('Математика')
                expect(page.locator('#schedule')).to_be_visible()
                saved = client.get('/api/day/2026-10-15').json
                assert len(saved['day']['lessons']) == 2

                # Reusing the current teacher at an exact daily/weekly limit works.
                cell.click()
                expect(page.locator('#lesson-picker-selected')).to_contain_text('Математика')
                page.locator(f'[data-picker-card="{first["id"]}"]').get_by_role('button', name='Алгебра', exact=True).click()
                page.locator('#lesson-picker-save').click()
                expect(page.locator('#lesson-picker-dialog')).not_to_be_visible()
                expect(cell).to_contain_text('Алгебра')

                # Advanced editing remains available for creating groups and notes.
                cell.click()
                page.locator('#lesson-picker-details').click()
                page.locator('#group-count').select_option('2')
                group = page.locator('.group-box').nth(1)
                group.locator('[data-field="teacher"]').fill(second['name'])
                group.locator('[data-field="subject"]').fill('Физика')
                group.locator('[data-field="rooms"]').fill('18')
                group.locator('[data-field="label"]').fill('Вторая группа')
                page.locator('#lesson-note').fill('Сохранить обе группы')
                page.locator('#lesson-form button[type="submit"]').click()
                expect(page.locator('#lesson-dialog')).not_to_be_visible()
                before_group = next(l for l in client.get('/api/day/2026-10-15').json['day']['lessons'] if l['class_id'] == 1)
                cell.click()
                expect(page.locator('#lesson-picker-group-label')).to_be_visible()
                expect(page.locator('#lesson-picker-group')).to_be_enabled()
                page.locator('#lesson-picker-group').select_option('1')
                expect(page.locator('#lesson-picker-current')).to_contain_text('Физика')
                expect(page.locator(f'[data-picker-card="{second["id"]}"]')).to_be_visible()
                page.locator(f'[data-picker-card="{second["id"]}"]').get_by_role('button', name='География', exact=True).click()
                page.locator('#lesson-picker-save').click()
                expect(page.locator('#lesson-picker-dialog')).not_to_be_visible()
                after_group = next(l for l in client.get('/api/day/2026-10-15').json['day']['lessons'] if l['class_id'] == 1)
                assert after_group['groups'][0] == before_group['groups'][0]
                assert after_group['groups'][1]['subject'] == 'География'
                assert after_group['groups'][1]['label'] == 'Вторая группа'
                assert after_group['note'] == 'Сохранить обе группы'

                # A concurrent edit is rejected; refresh shows the newly busy teacher.
                page.locator('#grid-wrap [data-class="1"][data-slot="2"]').click()
                page.locator(f'[data-picker-card="{second["id"]}"]').get_by_role('button', name='Физика', exact=True).click()
                latest = client.get('/api/day/2026-10-15').json
                latest['day']['lessons'].append({'class_id': 2, 'slot': 2, 'groups': [{'teacher_id': second['id'], 'teacher': second['name'], 'subject': 'Физика', 'rooms': ['18']}]})
                assert client.put('/api/day/2026-10-15', json={'day': latest['day'], 'revision': latest['revision']}).status_code == 200
                page.locator('#lesson-picker-save').click()
                expect(page.locator('#lesson-picker-error')).to_contain_text('Расписание изменилось')
                page.locator('#lesson-picker-refresh').click()
                expect(page.locator('#lesson-picker-unavailable-title')).to_contain_text('Недоступны')
                page.locator('#lesson-picker-unavailable > summary').click()
                expect(page.locator(f'[data-picker-unavailable="{second["id"]}"]')).to_contain_text('Класс 02, урок 2')
                expect(page.locator('#lesson-picker-save')).to_be_disabled()
                page.keyboard.press('Escape')
                expect(page.locator('#lesson-picker-dialog')).not_to_be_visible()
                assert not any(l['class_id'] == 1 and l['slot'] == 2 for l in client.get('/api/day/2026-10-15').json['day']['lessons'])

                # An unassigned class shows a useful empty state without unrelated teachers.
                before_cancel = client.get('/api/day/2026-10-15').json
                page.locator('#grid-wrap [data-class="3"][data-slot="1"]').click()
                expect(page.locator('#lesson-picker-status')).to_contain_text('нет учителей, назначенных этому классу')
                expect(page.locator('[data-picker-card]')).to_have_count(0)
                page.keyboard.press('Escape')
                expect(page.locator('#grid-wrap .is-editing')).to_have_count(0)
                assert client.get('/api/day/2026-10-15').json == before_cancel

                # Lesson zero can be selected and saved on a narrow screen.
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('button[data-shift="2"]').click()
                page.locator('#grid-wrap [data-class="25"][data-slot="0"]').click()
                expect(page.locator('#lesson-picker-time')).to_contain_text('13:15–13:55')
                page.locator(f'[data-picker-card="{second["id"]}"]').get_by_role('button', name='Физика', exact=True).click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('#lesson-picker-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
                page.screenshot(path=str(ROOT / 'test-output/cell-picker-mobile.png'))
                page.locator('#lesson-picker-save').click()
                expect(page.locator('#lesson-picker-dialog')).not_to_be_visible()
                expect(page.locator('#grid-wrap [data-class="25"][data-slot="0"]')).to_contain_text('Физика')
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Passed: direct timetable picker, class filtering, availability, subjects/rooms, replacement at load limits, group preservation, concurrency, empty state, keyboard and mobile lesson zero.')


if __name__ == '__main__':
    main()
