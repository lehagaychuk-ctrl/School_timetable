"""Check mismatched server/UI versions and the actual running application."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import threading
from ui_helpers import open_tab, show_schedule_notes
from app import create_app
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'startup.sqlite3')
        fixture_client = app.test_client()
        fixture_html = fixture_client.get('/').get_data(as_text=True)
        fixture_state = fixture_client.get('/api/state').json
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                for scenario in ('old_api', 'old_html'):
                    page = browser.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    if scenario == 'old_api':
                        def old_state(route):
                            data = dict(fixture_state)
                            data.pop('teachers')
                            route.fulfill(status=200, json=data)
                        page.route('**/api/state', old_state)
                    else:
                        def old_html(route):
                            html = fixture_html.replace('id="add-teacher"', 'id="old-add-teacher"')
                            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
                        page.route(f'http://127.0.0.1:{server.server_port}/', old_html)
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.wait_for_selector('.startup-error')
                    text = page.locator('.startup-error').inner_text()
                    assert 'Ctrl+C' in text and 'start.bat' in text
                    assert 'Cannot read properties' not in text
                    assert not errors, errors
                    page.close()
                page = browser.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_selector('[data-class="1"][data-slot="1"]')
                assert page.locator('.startup-error').count() == 0
                open_tab(page, "teachers")
                page.locator('#add-teacher').click()
                assert page.locator('#teacher-dialog').is_visible()
                browser.close()
        finally:
            server.shutdown()
    print('Startup checks passed: old API, old HTML, current version, teacher dialog.')


if __name__ == '__main__':
    main()
