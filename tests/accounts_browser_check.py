"""End-to-end local registration, curriculum, planning, resource views and school isolation."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import tempfile
import threading
from ui_helpers import open_tab, show_schedule_notes
from app import create_app
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server


def main():
    output = ROOT / 'test-output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(Path(temporary) / 'school.sqlite3')
        fixture = app.test_client()
        teacher = fixture.post('/api/teachers', json={'name': 'Учитель математики', 'subjects': ['Математика'], 'rooms': ['101'],
                                                     'class_ids': [1], 'weekly_hours': 4, 'daily_limit': 2}).json['teacher']
        server = make_server('127.0.0.1', 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1536, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/account')
                form = page.locator('#register-form')
                form.locator('[name=school]').fill('Тестовая школа')
                form.locator('[name=name]').fill('Тестовый пользователь')
                form.locator('[name=email]').fill('browser@school.example')
                form.locator('[name=password]').fill('Browser-Password-2026!')
                page.screenshot(path=str(output / '15-account-registration.png'), full_page=True)
                form.locator('[type=submit]').click()
                expect(page.locator('#recovery-result')).to_be_visible()
                assert len(page.locator('#recovery-code').inner_text()) > 20
                page.locator('#continue-to-app').click()
                open_tab(page, 'home')
                expect(page.locator('.metric-card')).to_have_count(8)
                expect(page.locator('#workspace-school')).to_have_text('Тестовая школа')
                expect(page.locator('.metric-0 strong')).to_have_text('47')
                expect(page.locator('.metric-1 strong')).to_have_text('1')
                assert fixture.get('/api/state').status_code == 401

                open_tab(page, "classes")
                page.locator('.class-row input').first.fill('5А')
                page.locator('#save-classes').click()
                expect(page.locator('#save-classes')).to_be_enabled()
                open_tab(page, "directories")
                page.locator('[data-catalog-kind="room"]').click()
                page.locator('#add-catalog-item').click()
                page.locator('#catalog-item-name').fill('102')
                page.locator('#catalog-item-capacity').fill('30')
                page.locator('#catalog-item-note').fill('Кабинет для проверки импорта и подбора')
                page.locator('#catalog-item-form [type=submit]').click()
                expect(page.locator('#catalog-item-dialog')).not_to_be_visible()
                expect(page.locator('#catalog-items')).to_contain_text('102')
                expect(page.locator('#catalog-items')).to_contain_text('30')

                open_tab(page, "rules")
                expect(page.locator('#rule-days input')).to_have_count(7)
                for i in (5, 6):
                    page.locator(f'#rule-days input[value="{i}"]').uncheck()
                page.locator('#rules-form [name=class_daily_limit]').fill('6')
                page.locator('#rules-form [name=subject_daily_limit]').fill('1')
                page.locator('#rules-form [type=submit]').click()
                expect(page.locator('#rules-form [type=submit]')).to_be_enabled()

                open_tab(page, "curriculum")
                expect(page.locator('#curriculum-list')).to_contain_text('не заполнена')
                page.locator('#add-course').click()
                page.locator('#course-class').select_option('1')
                page.locator('#course-teacher').select_option(str(teacher['id']))
                page.locator('#course-hours').fill('3')
                page.locator('#course-rooms input').first.check()
                page.locator('#course-form [type=submit]').click()
                expect(page.locator('#course-dialog')).not_to_be_visible()
                expect(page.locator('#curriculum-list')).to_contain_text('Математика')
                expect(page.locator('#curriculum-list')).to_contain_text('5А')

                open_tab(page, "autofill")
                page.locator('#autofill-date').fill('2026-10-19')
                page.locator('#preview-autofill').click()
                expect(page.locator('#apply-autofill')).to_be_enabled()
                expect(page.locator('#autofill-results')).to_contain_text('Будет добавлено уроков: 3')
                assert not page.request.get(base + '/api/day/2026-10-19').json()['day']['lessons']
                page.screenshot(path=str(output / '16-autofill-preview.png'), full_page=True)
                page.locator('#apply-autofill').click()
                expect(page.locator('#autofill-results')).to_contain_text('добавлены')
                assert len(page.request.get(base + '/api/day/2026-10-19').json()['day']['lessons']) == 1

                open_tab(page, "teacher_schedule")
                expect(page.locator('#teacher_schedule .resource-results')).to_contain_text('5А')
                page.locator('#teacher_schedule .resource-week').check()
                expect(page.locator('#teacher_schedule .resource-results .resource-day')).to_have_count(7)
                open_tab(page, "room_schedule")
                expect(page.locator('#room_schedule .resource-results')).to_contain_text('Математика')

                open_tab(page, "imports")
                page.locator('#import-text').fill('name;shift;print_group\nИмпортированный класс;2;Новый блок\n')
                page.locator('#preview-import').click()
                expect(page.locator('#apply-import')).to_be_enabled()
                assert len(page.request.get(base + '/api/state').json()['classes']) == 47
                page.locator('#apply-import').click()
                expect(page.locator('#import-results')).to_contain_text('добавлены')
                assert len(page.request.get(base + '/api/state').json()['classes']) == 48
                open_tab(page, "home")
                expect(page.locator('.metric-0 strong')).to_have_text('48')
                expect(page.locator('.metric-4 strong')).to_have_text('3')
                expect(page.locator('.metric-5 strong')).to_have_text('3')
                expect(page.locator('.metric-6 strong')).to_have_text('0')
                page.screenshot(path=str(output / '17-school-dashboard.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / '18-dashboard-mobile.png'), full_page=True)
                page.locator('#logout-account').click()
                expect(page.locator('#login-form')).to_be_visible()
                login = page.locator('#login-form')
                login.locator('[name=email]').fill('browser@school.example')
                login.locator('[name=password]').fill('wrong')
                login.locator('[type=submit]').click()
                expect(login.locator('.form-error')).to_contain_text('Неверная')
                login.locator('[name=password]').fill('Browser-Password-2026!')
                login.locator('[type=submit]').click()
                open_tab(page, 'home')
                expect(page.locator('.metric-0 strong')).to_have_text('48')

                # A second school's browser has neither the first school's classes nor teachers.
                second = browser.new_page(viewport={'width': 1000, 'height': 900})
                second.on('pageerror', lambda error: errors.append(str(error)))
                second.goto(base + '/account')
                registration = second.locator('#register-form')
                for key, value in [('school', 'Другая школа'), ('name', 'Другой пользователь'), ('email', 'other@school.example'), ('password', 'Other-Password-2026!')]:
                    registration.locator('[name=' + key + ']').fill(value)
                registration.locator('[type=submit]').click()
                expect(second.locator('#recovery-result')).to_be_visible()
                second.locator('#continue-to-app').click()
                open_tab(second, 'home')
                expect(second.locator('.metric-0 strong')).to_have_text('0')
                expect(second.locator('.metric-1 strong')).to_have_text('0')
                expect(page.locator('.metric-0 strong')).to_have_text('48')
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Browser checks passed: registration/recovery code/login/logout, school isolation, catalog, curriculum, rules, preview/apply, teacher/room schedules, import, live metrics, mobile layout; no JavaScript errors.')


if __name__ == '__main__':
    main()
