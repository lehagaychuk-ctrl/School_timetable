"""Acceptance check for teacher classes, working hours and workload in the UI."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import threading
from ui_helpers import open_tab, show_schedule_notes
from app import create_app, empty_day
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright


def main():
    output = ROOT / 'test-output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'constructor.sqlite3')
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client = app.test_client()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1536, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_selector('[data-class]')
                page.locator('#day-date').fill('2026-10-15')
                page.locator('#day-date').dispatch_event('change')
                page.wait_for_function("document.querySelector('#export-date').textContent.includes('15 октября')")
                open_tab(page, "teachers")
                page.locator('#add-teacher').click()
                page.locator('#teacher-name').fill('Иванова Анна Александровна')
                page.locator('#teacher-subjects').fill('Математика\nАлгебра')
                page.locator('#teacher-rooms').fill('14')
                page.locator('[data-teacher-class="1"]').check()
                page.locator('[data-class-hours="1"]').fill('2')
                page.locator('#teacher-weekly-hours').fill('3')
                page.locator('#teacher-daily-limit').fill('2')
                page.locator('[data-availability="3"]').fill('08:00–09:30')
                page.screenshot(path=str(output / '06-teacher-working-rules.png'), full_page=True)
                page.locator('#teacher-form button[type=submit]').click()
                page.wait_for_function("!document.querySelector('#teacher-dialog').open")
                main_teacher = client.get('/api/state').json['teachers'][0]
                assert main_teacher['class_ids'] == [1]
                assert main_teacher['weekly_hours'] == 3 and main_teacher['daily_limit'] == 2
                assert main_teacher['class_hours'] == {'1': 2}
                assert main_teacher['availability']['3'] == ['08:00–09:30']
                fixtures = [
                    {'name': 'Петров — другой класс', 'class_ids': [2]},
                    {'name': 'Сидорова — после 10 часов', 'class_ids': [1], 'availability_enabled': True, 'availability': {'3': ['10:00–12:00']}},
                    {'name': 'Смирнов — занят в другом классе', 'class_ids': [1, 2]},
                    {'name': 'Орлова — нагрузка выполнена', 'class_ids': [1], 'weekly_hours': 0},
                ]
                busy = None
                for item in fixtures:
                    response = client.post('/api/teachers', json={'subjects': ['Математика'], 'rooms': ['90'], **item})
                    assert response.status_code == 201, response.json
                    if item['name'].startswith('Смирнов'):
                        busy = response.json['teacher']
                body = empty_day()
                body['lessons'] = [{'class_id': 2, 'slot': 1, 'groups': [{'teacher_id': busy['id'], 'teacher': busy['name'], 'subject': 'Математика', 'rooms': ['90']}]}]
                assert client.put('/api/day/2026-10-15', json={'day': body, 'revision': 0}).status_code == 200
                page.reload()
                page.wait_for_selector('[data-class]', state='attached')
                open_tab(page, "schedule")
                page.locator('#day-date').fill('2026-10-15')
                page.locator('#day-date').dispatch_event('change')
                page.wait_for_function("document.querySelector('#export-date').textContent.includes('15 октября')")
                open_tab(page, "constructor")
                page.wait_for_selector('[data-builder-card]')
                assert page.locator('[data-builder-card]').count() == 1
                page.locator('#builder-show-unavailable').check()
                assert page.locator('[data-builder-card]').count() == 5
                assert page.locator('[data-builder-select]:disabled').count() == 4
                reasons = page.locator('#builder-teachers').inner_text()
                assert 'не указан в карточке' in reasons
                assert 'часы работы' in reasons
                assert 'Учитель занят' in reasons
                assert 'Недельная нагрузка выполнена' in reasons
                page.locator('#builder-busy-tab').click()
                assert page.locator('#builder-busy-teachers').is_visible()
                assert page.locator('#builder-teachers').is_hidden()
                busy_text = page.locator('#builder-busy-teachers').inner_text()
                assert 'Смирнов' in busy_text and 'Класс 02' in busy_text
                assert '08:00–08:40' in busy_text and '90' in busy_text
                assert page.locator('[data-busy-teacher]').count() == 1
                page.screenshot(path=str(output / '09-busy-teachers.png'), full_page=True)
                page.locator('#builder-busy-tab').press('ArrowLeft')
                assert page.locator('#builder-teachers').is_visible()
                page.locator(f'[data-builder-select="{main_teacher["id"]}"]').click()
                page.locator('#builder-subject').select_option('Математика')
                page.locator('#builder-add').click()
                page.wait_for_function("document.querySelector('#lesson-count').textContent === '2'")
                page.locator('#builder-slot').select_option('2')
                page.wait_for_function("document.querySelector('#builder-summary').textContent.includes('урок 2')")
                page.locator(f'[data-builder-select="{main_teacher["id"]}"]').click()
                page.locator('#builder-subject').select_option('Алгебра')
                page.locator('#builder-add').click()
                page.wait_for_function("document.querySelector('#lesson-count').textContent === '3'")
                page.locator('#builder-slot').select_option('3')
                page.wait_for_function("document.querySelector('#builder-summary').textContent.includes('урок 3')")
                row = page.locator('[data-builder-slot="3"]')
                row_box = row.bounding_box()
                time_box = row.locator('.builder-day-time').bounding_box()
                text_box = row.locator('.builder-day-content').bounding_box()
                assert time_box['x'] >= row_box['x'] + 10
                assert text_box['x'] >= time_box['x'] + time_box['width'] + 8
                assert page.locator(f'[data-builder-select="{main_teacher["id"]}"]').is_disabled()
                card = page.locator(f'[data-builder-card="{main_teacher["id"]}"]').inner_text()
                assert 'Нагрузка в этом классе выполнена' in card
                assert 'Достигнут предел на день' in card
                assert 'часы работы' in card
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(output / '07-constructor.png'), full_page=True)
                saved = client.get('/api/day/2026-10-15').json
                assigned = [l for l in saved['day']['lessons'] if l['class_id'] == 1]
                assert len(assigned) == 2
                assert {l['groups'][0]['subject'] for l in assigned} == {'Математика', 'Алгебра'}
                assert all(l['groups'][0]['teacher_id'] == main_teacher['id'] for l in assigned)
                page.set_viewport_size({'width': 390, 'height': 844})
                row_box = row.bounding_box()
                time_box = row.locator('.builder-day-time').bounding_box()
                text_box = row.locator('.builder-day-content').bounding_box()
                assert time_box['x'] >= row_box['x'] + 10
                assert text_box['x'] >= time_box['x'] + time_box['width'] + 8
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Constructor browser check passed: class assignments, working hours, workload, busy-teacher tab, class/time/room details, highlight spacing, adding lessons, mobile layout.')


if __name__ == '__main__':
    main()
