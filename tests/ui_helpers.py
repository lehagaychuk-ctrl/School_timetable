"""Use the same visible navigation and disclosure controls as a person."""


def open_tab(page, tab):
    page.wait_for_function("document.querySelector('#day-date')?.value")
    button = page.locator(f'.nav-item[data-tab="{tab}"]')
    group = button.locator('xpath=ancestor::details[contains(@class,"nav-group")]')
    if group.count() and not group.evaluate('(element) => element.open'):
        group.locator('summary').click()
    button.click()


def show_schedule_notes(page):
    details = page.locator('#schedule-extras')
    if not details.evaluate('(element) => element.open'):
        details.locator('summary').click()


def show_validation(page):
    details = page.locator('#schedule-validation')
    if not details.evaluate('(element) => element.open'):
        details.locator('summary').first.click()


def open_lesson_editor(page, class_id, slot):
    page.locator(f'#grid-wrap [data-class="{class_id}"][data-slot="{slot}"]').click()
    page.locator('#lesson-picker-details').click()
