"""Follow real validation links, including hidden groups, exports and lesson zero."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor'), str(ROOT / 'tests')]

import tempfile
import threading
from app import create_app, empty_day
from test_reference_layout import reference_fixture
from ui_helpers import open_tab, show_validation, open_lesson_editor
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'school.sqlite3')
        client = app.test_client()
        classes, _, _ = reference_fixture()
        assert client.put('/api/classes', json={'classes': classes}).status_code == 200
        day = empty_day()
        day['bells'] = {'1': {'3': '09:45–10:25', '7': '13:15–13:55'},
                        '2': {'0': '13:15–13:55', '1': '14:00–14:40'}}
        group = {'subject': 'Физика', 'teacher': 'Учитель А.А.', 'rooms': ['42']}
        day['lessons'] = [{'class_id': cid, 'slot': slot, 'groups': [group]} for cid, slot in [(1, 7), (34, 0)]]
        day['lessons'].append({'class_id': 29, 'slot': 3, 'groups': [{'subject': 'История', 'teacher': '', 'rooms': []}]})
        response = client.put('/api/day/2026-10-16', json={'day': day, 'revision': 0})
        assert response.status_code == 200, response.json
        before = client.get('/api/day/2026-10-16').json
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/?date=2026-10-16')
                expect(page.locator('#day-date')).to_have_value('2026-10-16')
                page.locator('#class-search').fill('Ничего не найдено')
                expect(page.locator('#grid-wrap [data-class]')).to_have_count(0)
                page.locator('#show-validation').click()
                conflict = page.locator('#issues .issue-link').filter(has_text='Кабинет 42:')
                conflict.focus()
                conflict.press('Enter')
                expect(page.locator('#class-search')).to_have_value('')
                expect(page.locator('button[data-shift="all"]')).to_have_class('active')
                expect(page.locator('#issue-focus')).to_be_visible()
                expect(page.locator('#grid-wrap .is-issue-target')).to_have_count(2)
                expect(page.locator('#grid-wrap [data-class="1"][data-slot="7"]')).to_be_focused()
                expect(page.locator('#lesson-dialog')).not_to_be_visible()
                # Both conflict cells stay marked; the zero lesson can be reached separately.
                for width in (1440, 390):
                    page.set_viewport_size({'width': width, 'height': 844})
                    page.locator('[data-issue-cell="34:0"]').click()
                    zero = page.locator('#grid-wrap [data-class="34"][data-slot="0"]')
                    expect(zero).to_be_focused()
                    assert zero.evaluate("e => getComputedStyle(e).backgroundColor") == 'rgb(250, 233, 229)'
                    assert zero.evaluate("e => {const r=e.getBoundingClientRect(), w=e.closest('#grid-wrap').getBoundingClientRect();return r.left>=w.left+145 && r.right<=w.right+1 && r.top>=w.top+40 && r.bottom<=w.bottom+1}")
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(ROOT / 'test-output' / 'clickable-error-mobile.png'))
                # Returning from another shift restores all affected lessons.
                page.locator('button[data-shift="1"]').click()
                page.locator('[data-issue-cell="34:0"]').click()
                expect(page.locator('#grid-wrap .is-issue-target')).to_have_count(2)
                page.locator('#clear-issue-focus').click()
                expect(page.locator('#issue-focus')).not_to_be_visible()
                expect(page.locator('#grid-wrap .is-issue-target')).to_have_count(0)
                # A warning in another print group is also actionable.
                page.locator('button[data-shift="2"]').click()
                show_validation(page)
                page.locator('#issues .issue-link').filter(has_text='10Б, урок 3: не указан учитель.').click()
                expect(page.locator('button[data-shift="1"]')).to_have_class('active')
                expect(page.locator('#class-group')).to_have_value('all')
                expect(page.locator('#grid-wrap [data-class="29"][data-slot="3"]')).to_be_focused()
                expect(page.locator('#grid-wrap .is-issue-target')).to_have_count(1)
                open_lesson_editor(page, 29, 3)
                expect(page.locator('#lesson-dialog')).to_be_visible()
                page.locator('[data-close="lesson-dialog"]').first.click()
                # Export validation offers the same real links back to the table.
                open_tab(page, 'documents')
                page.locator('#generate-preview').click()
                export_error = page.locator('#export-issues .issue-link').filter(has_text='Кабинет 42:')
                expect(export_error).to_be_visible()
                export_error.click()
                expect(page.locator('#schedule')).to_be_visible()
                expect(page.locator('#grid-wrap .is-issue-target')).to_have_count(2)
                assert client.get('/api/day/2026-10-16').json == before, 'Navigation must not save or change the schedule.'
                page.screenshot(path=str(ROOT / 'test-output' / 'clickable-error-highlight.png'))
                # Correcting the problem clears the selection and refreshes validation.
                open_lesson_editor(page, 1, 7)
                page.locator('#delete-lesson').click()
                expect(page.locator('#lesson-dialog')).not_to_be_visible()
                expect(page.locator('#save-status')).to_have_text('Сохранено в базе')
                expect(page.locator('#error-count')).to_have_text('0')
                expect(page.locator('#issue-focus')).not_to_be_visible()
                expect(page.locator('#grid-wrap .is-issue-target')).to_have_count(0)
                # Changing dates removes the previous error selection.
                page.locator('#day-date').fill('2026-10-17')
                page.locator('#day-date').dispatch_event('change')
                expect(page.locator('#export-date')).to_contain_text('17 октября')
                expect(page.locator('#issue-focus')).not_to_be_visible()
                show_validation(page)
                page.locator('#issues .issue-link').filter(has_text='нет действующих уроков').click()
                expect(page.locator('#constructor')).to_be_visible()
                expect(page.locator('#builder-class')).to_be_focused()
                # A school-wide issue opens and highlights its actual settings form.
                assert client.put('/api/rules', json={'school_days': [0], 'class_daily_limit': None,
                    'subject_daily_limit': None, 'avoid_windows': True}).status_code == 200
                open_tab(page, 'schedule')
                page.locator('#day-date').fill('2026-10-16')
                page.locator('#day-date').dispatch_event('change')
                expect(page.locator('#export-date')).to_contain_text('16 октября')
                show_validation(page)
                page.locator('#issues .issue-link').filter(has_text='неучебный день').click()
                expect(page.locator('#rules')).to_be_visible()
                expect(page.locator('#rules-form')).to_be_focused()
                expect(page.locator('#rules-form')).to_have_class('is-issue-target')
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Passed: exact cross-shift links, lesson zero, keyboard/mobile, warnings/exports, unchanged navigation data, correction/date clearing and rules settings.')


if __name__ == '__main__':
    main()
