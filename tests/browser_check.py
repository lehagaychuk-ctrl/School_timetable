"""Browser acceptance check against an isolated database; requires local Edge."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import json
import tempfile
import threading
from io import BytesIO

from ui_helpers import open_tab, show_schedule_notes, show_validation, open_lesson_editor
from app import create_app
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from pypdf import PdfReader
from openpyxl import load_workbook
from docx import Document


def main():
    output = ROOT / 'test-output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        server = make_server('127.0.0.1', 0, create_app(Path(temporary) / 'browser.sqlite3'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1536, 'height': 1100}, accept_downloads=True)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_selector('[data-class="1"][data-slot="1"]')
                page.locator('#day-date').fill('2026-10-15')
                page.locator('#day-date').dispatch_event('change')
                page.wait_for_function("document.querySelector('#export-date').textContent.includes('15 октября')")
                assert page.locator('.schedule-table thead th').count() == 26
                assert page.locator('[data-class="1"][data-slot="1"]').bounding_box()['width'] >= 150
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(output / '01-empty-editor.png'), full_page=True)

                # Build the teacher catalog through its UI and use a card as a constructor.
                open_tab(page, "teachers")
                for name, subjects, rooms in [('Иванова А.А.', 'Английский язык', '18,25'),
                                               ('Петрова Б.Б.', 'Английский язык', '26'),
                                               ('Сидорова В.В.', 'Математика\nАлгебра', '7')]:
                    page.locator('#add-teacher').click()
                    page.locator('#teacher-name').fill(name)
                    page.locator('#teacher-subjects').fill(subjects)
                    page.locator('#teacher-rooms').fill(rooms)
                    page.locator('[data-teacher-class="1"]').check()
                    page.locator('#teacher-form button[type=submit]').click()
                    page.wait_for_function("!document.querySelector('#teacher-dialog').open")
                assert page.locator('.teacher-card').count() == 3
                assert page.locator('#teacher-nav-count').inner_text() == '3'
                page.locator('#teacher-search').fill('Алгебра')
                assert page.locator('.teacher-card').count() == 1
                page.locator('#teacher-search').fill('')
                page.locator('.teacher-card').filter(has_text='Иванова А.А.').locator('[data-teacher-use]').click()
                page.wait_for_function("document.querySelector('#builder-selection').textContent === 'Иванова А.А.'")
                assert page.locator('#builder-subject').input_value() == 'Английский язык'
                assert page.locator('#builder-rooms').input_value() == '18,25'
                page.locator('#builder-add').click()
                page.wait_for_function("document.querySelector('#lesson-count').textContent === '1'")
                open_tab(page, "schedule")

                # Create two groups, including two rooms in one group.
                open_lesson_editor(page, 1, 1)
                assert page.locator('[data-field="teacher"]').input_value() == 'Иванова А.А.'
                assert page.locator('[data-field="subject"]').input_value() == 'Английский язык'
                assert page.locator('[data-field="rooms"]').input_value() == '18,25'
                page.locator('#group-count').select_option('2')
                boxes = page.locator('.group-box')
                for i, teacher, rooms in [(0, 'Иванова А.А.', '18,25'), (1, 'Петрова Б.Б.', '26')]:
                    boxes.nth(i).locator('[data-field="subject"]').fill('Английский язык')
                    boxes.nth(i).locator('[data-field="teacher"]').fill(teacher)
                    boxes.nth(i).locator('[data-field="rooms"]').fill(rooms)
                page.locator('#lesson-form button[type=submit]').click()
                page.wait_for_function("!document.querySelector('#lesson-dialog').open")
                assert 'Английский язык' in page.locator('[data-class="1"][data-slot="1"]').inner_text()

                # Create another lesson then swap occupied slots in the editor.
                page.locator('[data-class="1"][data-slot="2"]').click()
                page.locator('[data-picker-card]').filter(has_text='Сидорова В.В.').get_by_role('button', name='Математика', exact=True).click()
                page.locator('#lesson-picker-save').click()
                page.wait_for_function("document.querySelector('#lesson-count').textContent === '2'")
                open_tab(page, "schedule")
                open_lesson_editor(page, 1, 1)
                page.locator('#lesson-slot').select_option('2')
                page.locator('#lesson-form button[type=submit]').click()
                page.wait_for_function("!document.querySelector('#lesson-dialog').open")
                assert 'Математика' in page.locator('[data-class="1"][data-slot="1"]').inner_text()
                assert 'Английский язык' in page.locator('[data-class="1"][data-slot="2"]').inner_text()

                show_schedule_notes(page)
                page.locator('#notes-1').fill('Английский язык — занятия по подгруппам.')
                # Simulate a note being edited while an earlier save is in flight.
                race = {'pending': True}
                def slow_save(route):
                    if route.request.method == 'PUT' and race['pending']:
                        race['pending'] = False
                        response = route.fetch()
                        page.evaluate("""() => {const note=document.querySelector('#notes-2');note.value='Заметка во время сохранения';note.dispatchEvent(new Event('input',{bubbles:true}));}""")
                        route.fulfill(response=response)
                    else:
                        route.continue_()
                page.route('**/api/day/**', slow_save)
                page.locator('#save-day').click()
                page.wait_for_function("document.querySelector('#save-status').textContent === 'Сохранено в базе'")
                assert page.locator('#notes-2').input_value() == 'Заметка во время сохранения'
                day_date = page.locator('#day-date').input_value()
                assert page.request.get(f'http://127.0.0.1:{server.server_port}/api/day/{day_date}').json()['day']['notes']['2'] == 'Заметка во время сохранения'
                page.unroute('**/api/day/**', slow_save)
                show_validation(page)
                page.locator('#approve-day').click()
                page.wait_for_function("document.querySelector('#approval-status').textContent === 'Утверждено'")
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(output / '02-filled-editor.png'), full_page=True)

                # Names, shifts, and order are persisted without losing lessons.
                open_tab(page, "classes")
                page.locator('.class-row input').first.fill('5А')
                page.locator('#save-classes').click()
                page.wait_for_function("document.querySelector('#save-classes').disabled === false")
                open_tab(page, "templates")
                page.locator('[name="school_name"]').fill('Тестовая школа')
                page.locator('[name="director"]').fill('Директор И.И.')
                page.locator('[name="show_teachers"]').check()
                page.locator('#template-form button[type=submit]').click()
                page.wait_for_function("document.querySelector('#template-form button[type=submit]').disabled === false")

                # Exports must be real files, reflecting current data in all formats.
                open_tab(page, "documents")
                for fmt in ('pdf', 'xlsx', 'docx'):
                    page.locator('#export-format').select_option(fmt)
                    with page.expect_download() as result:
                        page.locator('[data-download="selected"]').click()
                    download = result.value
                    path = output / ('browser-timetable.' + fmt)
                    download.save_as(path)
                    if fmt == 'pdf':
                        text = '\n'.join(p.extract_text() for p in PdfReader(path).pages)
                        assert '18,25' in text
                    elif fmt == 'xlsx':
                        book = load_workbook(path)
                        text = '\n'.join(str(c.value) for ws in book for row in ws for c in row if c.value)
                        assert book.sheetnames == ['1 смена', '2 смена']
                    else:
                        doc = Document(path)
                        text = '\n'.join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
                    assert 'Английский язык' in text and 'Математика' in text
                    assert 'Иванова А.А.' in text and 'Директор И.И.' in text and '5А' in text
                assert page.locator('#pdf-canvas').bounding_box()['width'] < page.locator('#pdf-preview').bounding_box()['width']
                page.locator('#preview-next').click()
                page.wait_for_function("document.querySelector('#pdf-canvas').getAttribute('aria-label').includes('страница 2')")
                page.locator('#preview-zoom').select_option('125')
                page.wait_for_function("document.querySelector('#pdf-canvas').getBoundingClientRect().width > document.querySelector('#pdf-preview').clientWidth")
                page.locator('#preview-zoom').select_option('page-width')
                page.wait_for_function("document.querySelector('#pdf-canvas').getBoundingClientRect().width < document.querySelector('#pdf-preview').clientWidth")
                page.locator('#preview-previous').click()
                page.wait_for_function("document.querySelector('#pdf-canvas').getAttribute('aria-label').includes('страница 1')")
                page.screenshot(path=str(output / '03-documents.png'), full_page=True)

                # Date copy through the actual UI leaves the source intact.
                open_tab(page, "schedule")
                page.locator('#next-date').click()
                page.wait_for_function("document.querySelector('#lesson-count').textContent === '0'")
                page.locator('#copy-day').click()
                page.locator('#copy-form button[type=submit]').click()
                page.wait_for_function("!document.querySelector('#copy-dialog').open")
                assert page.locator('#lesson-count').inner_text() == '2'
                open_lesson_editor(page, 1, 1)
                page.locator('#lesson-cancelled').check()
                page.locator('#lesson-form button[type=submit]').click()
                page.wait_for_function("!document.querySelector('#lesson-dialog').open")
                assert page.locator('#lesson-count').inner_text() == '1'
                page.locator('#previous-date').click()
                page.wait_for_function("document.querySelector('#lesson-count').textContent === '2'")

                # Mobile layout: no viewport overflow; table itself scrolls.
                page.set_viewport_size({'width': 390, 'height': 844})
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(output / '04-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows'

                # Editing and archiving a used teacher keeps references and past lessons.
                page.set_viewport_size({'width': 1536, 'height': 1100})
                open_tab(page, "teachers")
                page.locator('.teacher-card').filter(has_text='Иванова А.А.').locator('[data-teacher-edit]').click()
                page.locator('#teacher-name').fill('Иванова Анна Александровна')
                page.locator('#teacher-form button[type=submit]').click()
                page.wait_for_function("!document.querySelector('#teacher-dialog').open")
                open_tab(page, "schedule")
                assert 'Иванова Анна Александровна' in page.locator('[data-class="1"][data-slot="2"]').inner_text()
                open_tab(page, "teachers")
                teacher_card = page.locator('.teacher-card').filter(has_text='Иванова Анна Александровна')
                teacher_card.locator('.teacher-overflow summary').click()
                teacher_card.locator('[data-teacher-archive]').click()
                page.wait_for_function("document.querySelector('#teacher-nav-count').textContent === '2'")
                assert page.locator('#teacher-options option').count() == 2
                page.locator('#teacher-filter').select_option('archived')
                assert page.locator('.teacher-card').count() == 1
                page.locator('.teacher-overflow summary').click()
                page.locator('[data-teacher-archive]').click()
                page.wait_for_function("document.querySelector('#teacher-nav-count').textContent === '3'")
                page.locator('#teacher-filter').select_option('active')
                assert page.locator('.teacher-card').count() == 3
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(output / '05-teacher-catalog.png'), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Browser acceptance passed: teacher catalog, constructor, rename/archive/restore, editing, groups, swaps, approval, three downloads, date isolation, mobile layout; no JavaScript errors.')


if __name__ == '__main__':
    main()
