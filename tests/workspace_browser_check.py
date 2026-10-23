"""Browser acceptance checks for class creation, optional hints and saved schedules."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import threading
from ui_helpers import open_tab, show_schedule_notes
from app import create_app, empty_day
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    output = ROOT / 'test-output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'workspace.sqlite3')
        client = app.test_client()
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                expect(page.locator('[data-class="1"][data-slot="1"]')).to_be_visible()
                expect(page.locator('#usage-hints')).to_be_visible()
                expect(page.locator('#usage-hints-steps li')).to_have_count(3)
                page.locator('#copy-day').click()
                expect(page.locator('#copy-dialog')).to_be_visible()
                expect(page.locator('#apply-schedule-template')).to_be_disabled()
                expect(page.locator('#copy-summary')).to_contain_text('Сохранить день')
                page.locator('#copy-source').select_option('weekly')
                expect(page.locator('#apply-schedule-template')).to_be_disabled()
                page.locator('#copy-dialog [data-close]').click()

                page.locator('#show-hints').uncheck()
                expect(page.locator('#usage-hints')).to_be_hidden()
                expect(page.locator('#empty-hint')).to_be_hidden()
                open_tab(page, "classes")
                expect(page.locator('#classes .hint')).to_be_hidden()
                page.reload()
                expect(page.locator('#class-list .class-row')).to_have_count(47)
                expect(page.locator('#show-hints')).not_to_be_checked()
                expect(page.locator('#usage-hints')).to_be_hidden()
                page.locator('#show-hints').check()
                expect(page.locator('#usage-hints-title')).to_contain_text('классы и смены')

                # Adding a class also preserves pending edits to existing classes.
                page.locator('.class-row input').first.fill('1А')
                page.locator('#add-class').click()
                page.locator('#new-class-name').fill('5А')
                page.locator('#new-class-shift').select_option('2')
                page.locator('#class-form button[type=submit]').click()
                expect(page.locator('#class-dialog')).not_to_be_visible()
                expect(page.locator('.class-row')).to_have_count(48)
                expect(page.locator('.class-row input').first).to_have_value('1А')
                expect(page.locator('#class-nav-count')).to_have_text('48')
                expect(page.locator('#class-count')).to_have_text('48')
                expect(page.locator('#classes-total')).to_have_text('(48)')
                new_class = client.get('/api/state').json['classes'][-1]
                assert new_class['name'] == '5А' and new_class['shift'] == 2
                page.locator('#add-class').click()
                page.locator('#new-class-name').fill('5а')
                page.locator('#class-form button[type=submit]').click()
                expect(page.locator('#class-error')).to_contain_text('уникальны')
                assert len(client.get('/api/state').json['classes']) == 48
                page.locator('#class-dialog [data-close]').first.click()
                open_tab(page, "teachers")
                page.locator('#add-teacher').click()
                expect(page.locator(f'[data-class-choice="{new_class["id"]}"]')).to_contain_text('5А')
                page.locator('#teacher-dialog [data-close]').first.click()
                assert page.locator(f'#builder-class option[value="{new_class["id"]}"]').count() == 1
                assert page.locator(f'#export-class option[value="{new_class["id"]}"]').count() == 1

                # Reuse an actual saved date, including its notes, on another date.
                source = empty_day()
                source['notes'] = {'1': 'Объявление первой смены', '2': 'Объявление второй смены'}
                source['lessons'] = [{'class_id': new_class['id'], 'slot': 1, 'groups': [{'subject': 'Алгебра', 'teacher': '', 'rooms': ['33']}]}]
                assert client.put('/api/day/2026-10-15', json={'day': source}).status_code == 200
                assert client.post('/api/day/2026-10-15/weekly', json={}).status_code == 200
                source_snapshot = client.get('/api/day/2026-10-15').json
                open_tab(page, "schedule")
                page.locator('#day-date').fill('2026-10-19')
                page.locator('#day-date').dispatch_event('change')
                expect(page.locator('#export-date')).to_contain_text('19 октября')
                page.locator('#copy-day').click()
                page.locator('#copy-saved-date').select_option('2026-10-15')
                expect(page.locator('#copy-summary')).to_contain_text('Уроков: 1 · классов: 1')
                expect(page.locator('#copy-target')).to_contain_text('19 октября')
                assert not page.locator('#copy-saved-date option[value="2026-10-19"]').count()
                page.screenshot(path=str(output / '11-saved-schedule-picker.png'), full_page=True)
                page.locator('#apply-schedule-template').click()
                expect(page.locator('#copy-dialog')).not_to_be_visible()
                expect(page.locator('#lesson-count')).to_have_text('1')
                expect(page.locator('#notes-2')).to_have_value('Объявление второй смены')
                assert client.get('/api/day/2026-10-15').json == source_snapshot

                # A weekly template can be applied on a different weekday, on mobile too.
                page.locator('#day-date').fill('2026-10-20')
                page.locator('#day-date').dispatch_event('change')
                expect(page.locator('#export-date')).to_contain_text('20 октября')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('#copy-day').click()
                page.locator('#copy-source').select_option('weekly')
                page.locator('#copy-weekday').select_option('3')
                expect(page.locator('#copy-weekday-label')).to_be_visible()
                expect(page.locator('#apply-schedule-template')).to_be_enabled()
                bounds = page.locator('#copy-dialog').bounding_box()
                assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
                page.locator('#apply-schedule-template').click()
                expect(page.locator('#lesson-count')).to_have_text('1')
                assert client.get('/api/day/2026-10-20').json['day']['lessons'][0]['class_id'] == new_class['id']
                page.locator('#show-validation').click()
                expect(page.locator('#issues')).to_be_visible()
                page.locator('#show-hints').uncheck()
                expect(page.locator('#issues')).to_be_visible()
                open_tab(page, "classes")
                page.locator('#show-hints').check()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / '10-classes-and-hints-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.screenshot(path=str(output / '12-classes-and-hints.png'), full_page=True)
                page.reload()
                expect(page.locator('.class-row')).to_have_count(48)
                expect(page.locator('#show-hints')).to_be_checked()
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Browser checks passed: class creation, duplicate rejection, preserved edits, hints preference, saved-day and weekday templates, mobile layout; no JavaScript errors.')


if __name__ == '__main__':
    main()
