"""Check the sample print preset, announcements, actual PDF and Office rendering."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor'), str(ROOT / 'tests')]

import subprocess
import base64
import tempfile
import threading
from pypdf import PdfReader
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from ui_helpers import open_tab, show_schedule_notes
from app import create_app
from test_reference_layout import reference_fixture


def main():
    output = ROOT / 'test-output'
    output.mkdir(exist_ok=True)
    classes, day, settings = reference_fixture()
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'reference.sqlite3')
        client = app.test_client()
        assert client.put('/api/classes', json={'classes': classes}).status_code == 200
        without_notes = {**day, 'print_notes': []}
        assert client.put('/api/day/2026-10-16', json={'day': without_notes}).status_code == 200
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1536, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                expect(page.locator('#day-date')).to_be_enabled()
                page.locator('#day-date').fill('2026-10-16')
                page.locator('#day-date').dispatch_event('change')
                expect(page.locator('#export-date')).to_contain_text('16 октября')
                # Class groups change only the view; searching still reaches other groups.
                before_filter = client.get('/api/day/2026-10-16').json
                expect(page.locator('.schedule-table thead th')).to_have_count(11)
                page.locator('#class-group').select_option('all')
                expect(page.locator('.schedule-table thead th')).to_have_count(35)
                page.locator('#class-search').fill('9И')
                expect(page.locator('.schedule-table thead th')).to_have_count(3)
                expect(page.locator('#class-group')).to_be_disabled()
                page.locator('#class-search').fill('')
                page.locator('#class-group').select_option(index=0)
                page.locator('[data-shift="2"]').click()
                expect(page.locator('.schedule-table thead th')).to_have_count(10)
                page.locator('[data-shift="1"]').click()
                expect(page.locator('.schedule-table thead th')).to_have_count(11)
                assert client.get('/api/day/2026-10-16').json == before_filter
                show_schedule_notes(page)
                for index, note in enumerate(day['print_notes']):
                    page.locator('#add-print-note').click()
                    row = page.locator(f'[data-print-note="{index}"]')
                    row.locator('select').select_option(str(note['class_id']))
                    row.locator('textarea').fill(note['text'])
                page.locator('#save-day').click()
                expect(page.locator('#save-status')).to_have_text('Сохранено в базе')
                assert client.get('/api/day/2026-10-16').json['day']['print_notes'] == day['print_notes']

                open_tab(page, "classes")
                for index in range(27, 33):
                    page.locator(f'.class-row[data-index="{index}"] [data-print-group]').fill('10–11 классы')
                page.locator('#save-classes').click()
                expect(page.locator('#save-classes')).to_be_enabled()
                assert client.get('/api/state').json['classes'][27]['print_group'] == '10–11 классы'
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width': 1536, 'height': 1100})
                open_tab(page, "templates")
                page.locator('[name="school_name"]').fill('Тестовая школа')
                page.locator('[name="director"]').fill('Директор И.И.')
                for shift in ('1', '2'):
                    page.locator('#bells-' + shift).fill('\n'.join(settings['bells'][shift]))
                page.locator('#apply-reference-layout').click()
                expect(page.locator('[name="layout"]')).to_have_value('reference')
                expect(page.locator('[name="row_height"]')).to_have_value('12')
                expect(page.locator('[name="school_name"]')).to_have_value('Тестовая школа')
                expect(page.locator('[name="director"]')).to_have_value('Директор И.И.')
                page.locator('#template-form button[type=submit]').click()
                expect(page.locator('#template-form button[type=submit]')).to_be_enabled()
                persisted = client.get('/api/state').json['settings']
                assert persisted['template']['layout'] == 'reference'
                assert persisted['bells'] == settings['bells']
                page.screenshot(path=str(output / '13-reference-template-settings.png'), full_page=True)

                open_tab(page, "documents")
                page.locator('#generate-preview').click()
                expect(page.locator('#preview-page-count')).to_have_text('из 2')
                expect(page.locator('#pdf-canvas')).to_be_visible()
                expect(page.locator('#generate-preview')).to_be_enabled()
                def save_canvas(filename):
                    data = page.locator('#pdf-canvas').evaluate("canvas => canvas.toDataURL('image/png').split(',')[1]")
                    (output / filename).write_bytes(base64.b64decode(data))
                save_canvas('reference-page-1.png')
                page.locator('#preview-next').click()
                page.wait_for_function("document.querySelector('#pdf-canvas').getAttribute('aria-label').includes('страница 2')")
                expect(page.locator('#preview-next')).to_be_enabled()
                save_canvas('reference-page-2.png')
                for fmt in ('pdf', 'xlsx', 'docx'):
                    exported = client.get(f'/api/export/2026-10-16/{fmt}?scope=both')
                    assert exported.status_code == 200, exported.json
                    (output / ('reference-timetable.' + fmt)).write_bytes(exported.data)

                soffice = Path(r'C:\Program Files\LibreOffice\program\soffice.exe')
                for fmt in ('docx', 'xlsx'):
                    destination = output / ('reference-rendered-' + fmt)
                    destination.mkdir(exist_ok=True)
                    subprocess.run([str(soffice), '-env:UserInstallation=' + (output / ('reference-profile-' + fmt)).as_uri(),
                                    '--headless', '--convert-to', 'pdf', '--outdir', str(destination),
                                    str(output / ('reference-timetable.' + fmt))], check=True, timeout=60,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                    rendered = destination / 'reference-timetable.pdf'
                    pdf = PdfReader(rendered)
                    assert len(pdf.pages) == 2, (fmt, len(pdf.pages))
                    text = ' '.join(' '.join(p.extract_text() for p in pdf.pages).split())
                    assert all(c['name'] in text for c in classes), fmt
                    assert all(note['text'] in text for note in day['print_notes']), fmt
                    rendered_bytes = rendered.read_bytes()
                    def serve_rendered(route):
                        route.fulfill(status=200, content_type='application/pdf', body=rendered_bytes)
                    page.route('**/api/export/**/pdf?**', serve_rendered)
                    page.locator('#generate-preview').click()
                    expect(page.locator('#generate-preview')).to_be_enabled()
                    expect(page.locator('#preview-page-count')).to_have_text('из 2')
                    page.wait_for_function("document.querySelector('#pdf-canvas').getAttribute('aria-label').includes('страница 1')")
                    save_canvas('reference-' + fmt + '-page-1.png')
                    page.unroute('**/api/export/**/pdf?**')
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Reference layout passed: manual groups, daily announcements, preset and preserved metadata, two PDF pages, editable Excel/Word rendered by LibreOffice to two pages with complete text, mobile layout, no JavaScript errors.')


if __name__ == '__main__':
    main()
