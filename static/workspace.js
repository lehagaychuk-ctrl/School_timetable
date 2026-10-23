'use strict';
export const workspaceTabs = {
  home: ['Главная', 'Школа, учебная нагрузка и состояние расписания.'],
  bells: ['Звонки', 'Время уроков первой и второй смены.'],
  directories: ['Справочники', 'Классы, учителя, предметы и кабинеты.'],
  curriculum: ['Учебная программа', 'Предметы и недельные часы по каждому классу.'],
  teacher_schedule: ['Расписание учителей', 'Занятия преподавателя на день или неделю.'],
  room_schedule: ['Расписание кабинетов', 'Занятость кабинета на день или неделю.'],
  autofill: ['Автоподбор', 'Предложение расписания недели по программе и правилам школы.'],
  rules: ['Правила', 'Учебные дни, дневные пределы и предпочтения подбора.'],
  imports: ['Импорт', 'Проверка и загрузка справочников, резервная копия школы.'],
  help: ['Справка', 'Последовательность работы со школьным расписанием.'],
};
export const workspaceHints = Object.fromEntries(Object.keys(workspaceTabs).map(key => [key, {
  home: ['Счётчики показывают данные вашей школы; нагрузка и размещение считаются за неделю выбранной даты.', 'Начните со справочников, затем заполните учебную программу и правила.', 'Для просмотра занятий выберите расписание классов, учителей или кабинетов.'],
  bells: ['Задайте интервалы по одному на строку, до 12 уроков для каждой смены.', 'Проверяются реальные интервалы времени, в том числе между сменами.', 'Сохранение звонков снимает утверждение расписаний, чтобы их можно было проверить заново.'],
  directories: ['Добавьте предметы и кабинеты. Уже встречающиеся в расписаниях и карточках учителей записи собираются автоматически.', 'Классы и учителя редактируются соответствующими кнопками в этом разделе.', 'Архивные записи сохраняют историю. Переименование обновляет существующие занятия.'],
  curriculum: ['В карточке учителя отметьте классы и предметы, с которыми он работает.', 'Добавьте строку нагрузки: класс, учитель, предмет и уроки в неделю.', 'Кабинеты можно ограничить списком. Без ограничения подбор использует свободные кабинеты справочника.'],
  teacher_schedule: ['Выберите преподавателя и дату, затем нажмите «Показать».', 'Переключатель «Вся неделя» включает сохранённые дни и недельные шаблоны там, где нет расписания даты.', 'Для изменения урока откройте расписание соответствующего класса.'],
  room_schedule: ['Выберите кабинет и дату, затем нажмите «Показать».', 'Показываются классы, предметы, преподаватели и подгруппы с фактическим временем урока.', 'Недельные шаблоны отмечаются отдельно, чтобы отличать их от сохранённых дат.'],
  autofill: ['Заполните учебную программу и выберите любую дату нужной недели.', 'Нажмите «Подобрать расписание недели». Уже заданные уроки сохраняют места; оставшиеся часы размещаются по ограничениям.', 'Проверьте предложенные уроки и неразмещённые часы, затем нажмите «Применить». Если данные изменились, выполните подбор заново.'],
  rules: ['Отметьте учебные дни школы и при необходимости ограничьте уроки класса и предмета на день.', 'Рабочие часы и индивидуальные пределы преподавателей задаются в их карточках.', 'Уменьшение окон — предпочтение автоподбора; занятость и заданные пределы проверяются обязательно.'],
  imports: ['Скачайте CSV-шаблон выбранного типа и заполните его без изменения названий столбцов.', 'В полях subjects, rooms и classes перечисляйте значения через запятую; в учебной программе используйте точные названия справочников.', 'Сначала проверьте файл, затем добавьте записи. Существующие названия пропускаются.'],
  help: ['Следуйте последовательности ниже — от звонков и справочников до учебной программы и подбора.', 'Подсказки можно отключать и включать переключателем вверху страницы.', 'Пароль восстанавливается кодом, выданным при регистрации аккаунта.'],
}[key]]));

export function initializeWorkspace(context) {
  const {state, api, action, toast, saveDay, refreshState, loadDay, changeDate, openTab, escapeHTML: h, formatDate} = context;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  let data = null, kind = 'subject', catalogEdit = null, courseEdit = null, autoRun = null, importRun = null, requestVersion = 0;
  const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const options = (items, selected) => items.map(item => `<option value="${item.id}" ${item.id === selected ? 'selected' : ''}>${h(item.name)}</option>`).join('');
  const empty = message => `<p class="workspace-empty">${h(message)}</p>`;
  const table = (heads, rows) => `<table class="workspace-table"><thead><tr>${heads.map(v => `<th>${h(v)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
  const cell = v => `<td>${h(v)}</td>`;
  async function fetchData() { data = await api('/api/planning?date=' + state.date); return data; }
  async function updateAll() { await refreshState(); await loadDay(state.date); await fetchData(); }
  async function formAction(form, fn) {
    const button = $('[type=submit]', form), error = $('.form-error', form);
    button.disabled = true; error.textContent = '';
    try { await fn(); } catch (reason) { error.textContent = reason.message; }
    finally { button.disabled = false; }
  }
  function renderHome() {
    $('#workspace-date').value = state.date;
    $('#workspace-school').textContent = state.account?.school || state.settings.template.school_name || 'Школьное расписание';
    $('#workspace-user').textContent = state.account ? `${state.account.name} · ${state.account.email}` : 'Локальная школа · зарегистрируйте аккаунт для входа';
    const metrics = [['Классы', 'classes', 'справочник', 'classes'], ['Учителя', 'teachers', 'справочник', 'teachers'], ['Кабинеты', 'rooms', 'справочник', 'directories'], ['Предметы', 'subjects', 'справочник', 'directories'], ['Часов в программе', 'hours', 'за неделю', 'curriculum'], ['Уроков в сетке', 'placed', 'за неделю', 'schedule'], ['Без места', 'unplaced', 'часов программы', 'autofill'], ['Нарушения', 'violations', 'на выбранную дату', 'schedule']];
    const metric = i => {
      const [label, key, description, tab] = metrics[i];
      return `<button class="metric-card metric-${i}" data-metric-tab="${tab}"><span>${label}</span><strong>${data.metrics[key]}</strong><small>${description}</small></button>`;
    };
    $('#workspace-metrics').innerHTML = [0, 1, 5, 7].map(metric).join('') + `<details class="metrics-more"><summary>Подробная статистика</summary><div class="workspace-metrics">${[2, 3, 4, 6].map(metric).join('')}</div></details>`;
  }
  function renderCatalog() {
    $$('[data-catalog-kind]').forEach(button => button.classList.toggle('active', button.dataset.catalogKind === kind));
    const query = $('#catalog-item-search').value.trim().toLocaleLowerCase('ru-RU');
    const items = data.catalog.filter(item => item.kind === kind && ($('#catalog-item-archived').checked || item.active) && (item.name + ' ' + item.note).toLocaleLowerCase('ru-RU').includes(query));
    $('#catalog-items').innerHTML = items.length ? table(['Название', kind === 'room' ? 'Вместимость' : 'Примечание', 'Статус', 'Действия'], items.map(item => `<tr>${cell(item.name)}${cell(kind === 'room' ? item.capacity || '—' : item.note || '—')}${cell(item.active ? 'Действует' : 'В архиве')}<td><div class="workspace-links"><button class="button" data-edit-item="${item.id}">Изменить</button><button class="button" data-archive-item="${item.id}">${item.active ? 'В архив' : 'Восстановить'}</button></div></td></tr>`)) : empty('Записей пока нет. Добавьте предмет или кабинет.');
  }
  function renderCurriculum() {
    const selected = $('#curriculum-filter').value;
    $('#curriculum-filter').innerHTML = '<option value="">Все классы</option>' + options(state.classes);
    $('#curriculum-filter').value = selected;
    $('#curriculum-week').textContent = 'Нагрузка на неделю с ' + formatDate(data.week_start);
    const rows = data.curriculum.filter(c => !$('#curriculum-filter').value || String(c.class_id) === $('#curriculum-filter').value);
    $('#curriculum-list').innerHTML = rows.length ? table(['Класс', 'Предмет', 'Учитель', 'План', 'Размещено', 'Осталось', 'Кабинеты', 'Действия'], rows.map(course => `<tr>${[course.class_name, course.subject, course.teacher_name, course.weekly_hours, course.placed_hours, course.remaining_hours, course.rooms.join(', ') || 'Свободный'].map(cell).join('')}<td><div class="workspace-links"><button class="button" data-edit-course="${course.id}">Изменить</button><button class="button danger" data-delete-course="${course.id}">Удалить</button></div></td></tr>`)) : empty('Учебная программа не заполнена. Добавьте недельную нагрузку.');
  }
  function courseChoices(resetTeacher = false) {
    const selectedTeacher = resetTeacher ? null : Number($('#course-teacher').value);
    const teachers = state.teachers.filter(t => t.active && t.class_ids.includes(Number($('#course-class').value)));
    $('#course-teacher').innerHTML = options(teachers, selectedTeacher);
    if (!teachers.length) $('#course-teacher').innerHTML = '<option value="">Сначала назначьте учителя этому классу</option>';
    const teacher = teachers.find(t => t.id === Number($('#course-teacher').value));
    const selectedSubject = Number($('#course-subject').value);
    const subjects = data.catalog.filter(c => c.kind === 'subject' && c.active && teacher?.subjects.some(s => s.trim().toLocaleLowerCase('ru-RU') === c.name.trim().toLocaleLowerCase('ru-RU')));
    $('#course-subject').innerHTML = options(subjects, selectedSubject);
    if (!subjects.length) $('#course-subject').innerHTML = '<option value="">В карточке учителя нет предметов</option>';
  }
  function editCourse(id = null) {
    courseEdit = data.curriculum.find(c => c.id === id) || null;
    $('#course-class').innerHTML = options(state.classes, courseEdit?.class_id);
    $('#course-teacher').innerHTML = options(state.teachers, courseEdit?.teacher_id);
    $('#course-subject').innerHTML = options(data.catalog, courseEdit?.subject_id);
    courseChoices();
    $('#course-hours').value = courseEdit?.weekly_hours || 1;
    $('#course-rooms').innerHTML = data.catalog.filter(c => c.kind === 'room' && c.active).map(room => `<label class="checkbox"><input type="checkbox" value="${room.id}" ${(courseEdit?.room_ids || []).includes(room.id) ? 'checked' : ''}>${h(room.name)}</label>`).join('') || empty('Сначала добавьте кабинеты в справочник.');
    $('.form-error', $('#course-form')).textContent = '';
    $('#course-dialog').showModal();
  }
  async function renderResource(panel) {
    const type = panel.dataset.resourceKind;
    const picker = $('.resource-picker', panel), old = Number(picker.value);
    const items = type === 'teacher' ? state.teachers : data.catalog.filter(c => c.kind === 'room');
    picker.innerHTML = options(items, old);
    const dateInput = $('.resource-date', panel);
    if (!dateInput.value) dateInput.value = state.date;
    if (!picker.value) { $('.resource-results', panel).innerHTML = empty(type === 'teacher' ? 'Сначала добавьте учителей.' : 'Сначала добавьте кабинеты.'); return; }
    const result = await api(`/api/resource-schedule/${dateInput.value}?kind=${type}&id=${picker.value}&week=${$('.resource-week', panel).checked ? 1 : 0}`);
    $('.resource-results', panel).innerHTML = result.days.map(day => `<article class="resource-day"><h3>${h(formatDate(day.date))}${day.source === 'weekly' ? ' <small>Недельный шаблон</small>' : ''}</h3>${day.lessons.length ? table(['Время', 'Класс', 'Предмет', 'Учитель', 'Кабинеты'], day.lessons.map(lesson => `<tr>${[lesson.time || 'Не задано', lesson.class_name, lesson.subject + (lesson.group ? ' · ' + lesson.group : ''), lesson.teacher || 'Не указан', lesson.rooms.join(', ')].map(cell).join('')}</tr>`)) : empty('Занятий нет.')}</article>`).join('');
  }
  function renderRules() {
    $('#rule-days').innerHTML = weekdays.map((name, i) => `<label class="checkbox"><input type="checkbox" value="${i}" ${data.rules.school_days.includes(i) ? 'checked' : ''}>${name}</label>`).join('');
    for (const key of ['class_daily_limit', 'subject_daily_limit']) $('#rules-form').elements.namedItem(key).value = data.rules[key] ?? '';
    $('#rules-form').elements.namedItem('avoid_windows').checked = data.rules.avoid_windows;
  }
  async function open(tab) {
    if (!workspaceTabs[tab]) return;
    const version = ++requestVersion;
    await fetchData();
    if (version !== requestVersion) return;
    if (tab === 'home') renderHome();
    if (tab === 'directories') renderCatalog();
    if (tab === 'curriculum') renderCurriculum();
    if (tab === 'rules') renderRules();
    if (tab === 'bells') for (const shift of [1, 2]) $('#workspace-bells-' + shift).value = state.settings.bells[String(shift)].join('\n');
    if (tab.endsWith('_schedule')) await renderResource($('#' + tab));
    if (tab === 'autofill' && !$('#autofill-date').value) $('#autofill-date').value = state.date;
  }
  $('#workspace-metrics').addEventListener('click', event => { const button = event.target.closest('[data-metric-tab]'); if (button) openTab(button.dataset.metricTab); });
  $('#workspace-date').addEventListener('change', event => action(null, async () => { await changeDate(event.target.value); await open('home'); }));
  $('#workspace-bells-form').addEventListener('submit', event => { event.preventDefault(); formAction(event.target, async () => { await saveDay(); const bells = Object.fromEntries([1, 2].map(i => [String(i), $('#workspace-bells-' + i).value.split('\n').map(v => v.trim()).filter(Boolean)])); await api('/api/settings', 'PUT', {template: state.settings.template, bells}); await updateAll(); toast('Звонки сохранены'); }); });
  $$('[data-catalog-kind]').forEach(button => button.addEventListener('click', () => { kind = button.dataset.catalogKind; renderCatalog(); }));
  for (const id of ['catalog-item-search', 'catalog-item-archived']) $('#' + id).addEventListener('input', renderCatalog);
  function editItem(id = null) {
    catalogEdit = data.catalog.find(c => c.id === id) || null;
    $('#catalog-item-title').textContent = kind === 'room' ? 'Кабинет' : 'Предмет';
    $('#catalog-item-name').value = catalogEdit?.name || ''; $('#catalog-item-name').maxLength = kind === 'room' ? 40 : 120;
    $('#catalog-item-note').value = catalogEdit?.note || ''; $('#catalog-item-capacity').value = catalogEdit?.capacity || '';
    $('#catalog-capacity-label').hidden = kind !== 'room';
    $('.form-error', $('#catalog-item-form')).textContent = '';
    $('#catalog-item-dialog').showModal();
  }
  $('#add-catalog-item').addEventListener('click', () => editItem());
  $('#catalog-items').addEventListener('click', event => {
    const edit = event.target.closest('[data-edit-item]'), archive = event.target.closest('[data-archive-item]');
    if (edit) editItem(Number(edit.dataset.editItem));
    if (archive) action(archive, async () => { await saveDay(); const item = data.catalog.find(c => c.id === Number(archive.dataset.archiveItem)); await api('/api/catalog/' + item.id, 'PUT', {...item, active: !item.active}); await updateAll(); renderCatalog(); });
  });
  $('#catalog-item-form').addEventListener('submit', event => { event.preventDefault(); formAction(event.target, async () => {
    await saveDay(); const capacity = $('#catalog-item-capacity').value;
    await api('/api/catalog' + (catalogEdit ? '/' + catalogEdit.id : ''), catalogEdit ? 'PUT' : 'POST', {...catalogEdit, kind, name: $('#catalog-item-name').value, note: $('#catalog-item-note').value, capacity: kind === 'room' && capacity ? Number(capacity) : null});
    await updateAll(); $('#catalog-item-dialog').close(); renderCatalog(); toast('Справочник сохранён');
  }); });
  $('#add-course').addEventListener('click', () => editCourse());
  $('#course-class').addEventListener('change', () => courseChoices(true));
  $('#course-teacher').addEventListener('change', () => courseChoices());
  $('#curriculum-filter').addEventListener('change', renderCurriculum);
  $('#curriculum-list').addEventListener('click', event => {
    const edit = event.target.closest('[data-edit-course]'), remove = event.target.closest('[data-delete-course]');
    if (edit) editCourse(Number(edit.dataset.editCourse));
    if (remove) action(remove, async () => { const item = data.curriculum.find(c => c.id === Number(remove.dataset.deleteCourse)); await api('/api/curriculum/' + item.id, 'DELETE', {revision: item.revision}); await fetchData(); renderCurriculum(); toast('Строка программы удалена; сохранённые уроки остаются в расписании.'); });
  });
  $('#course-form').addEventListener('submit', event => { event.preventDefault(); formAction(event.target, async () => {
    const body = {class_id: Number($('#course-class').value), teacher_id: Number($('#course-teacher').value), subject_id: Number($('#course-subject').value), weekly_hours: Number($('#course-hours').value), room_ids: $$('input:checked', $('#course-rooms')).map(v => Number(v.value)), revision: courseEdit?.revision};
    await api('/api/curriculum' + (courseEdit ? '/' + courseEdit.id : ''), courseEdit ? 'PUT' : 'POST', body); await fetchData(); renderCurriculum(); $('#course-dialog').close(); toast('Учебная программа сохранена');
  }); });
  $$('[data-resource-kind]').forEach(panel => {
    for (const selector of ['.resource-picker', '.resource-date', '.resource-week']) $(selector, panel).addEventListener('change', () => action(null, () => renderResource(panel)));
    $('.resource-refresh', panel).addEventListener('click', event => action(event.currentTarget, () => renderResource(panel)));
  });
  $('#rules-form').addEventListener('submit', event => { event.preventDefault(); formAction(event.target, async () => {
    await saveDay(); const body = {school_days: $$('input:checked', $('#rule-days')).map(v => Number(v.value)), avoid_windows: event.target.elements.namedItem('avoid_windows').checked};
    for (const key of ['class_daily_limit', 'subject_daily_limit']) { const value = event.target.elements.namedItem(key).value; body[key] = value ? Number(value) : null; }
    await api('/api/rules', 'PUT', body); await updateAll(); toast('Правила сохранены. Расписание проверено заново.');
  }); });
  $('#autofill-date').addEventListener('change', () => { autoRun = null; $('#apply-autofill').disabled = true; $('#autofill-results').innerHTML = ''; });
  $('#preview-autofill').addEventListener('click', event => action(event.currentTarget, async () => {
    autoRun = null; $('#apply-autofill').disabled = true; $('#autofill-error').textContent = ''; $('#autofill-results').textContent = 'Подбираем свободные ячейки…';
    try {
      await saveDay(); const result = await api('/api/autofill/preview', 'POST', {date: $('#autofill-date').value}); autoRun = result.run_id;
      $('#autofill-results').innerHTML = `<p class="workspace-result">Будет добавлено уроков: <strong>${result.added}</strong>. Не размещено часов: <strong>${result.unplaced_hours}</strong>.</p>` + (result.unplaced.length ? `<div class="issue warning">${result.unplaced.map(c => `<p><strong>${h(c.class_name)} · ${h(c.subject)} · ${h(c.teacher_name)}: ${c.hours} ч.</strong><br>${c.reasons.map(h).join(' ')}</p>`).join('')}</div>` : '') + (result.proposed.length ? `<div class="workspace-table-scroll">${table(['Дата', 'Урок', 'Время', 'Класс', 'Предмет', 'Учитель', 'Кабинеты'], result.proposed.map(p => `<tr>${[formatDate(p.date), p.slot, p.time, p.class_name, p.subject, p.teacher, p.rooms.join(', ')].map(cell).join('')}</tr>`))}</div>` : empty('Новых назначений нет. Проверьте программу и причины ограничений.'));
      $('#apply-autofill').disabled = !result.added;
    } catch (reason) { $('#autofill-results').innerHTML = ''; $('#autofill-error').textContent = reason.message; }
  }));
  $('#apply-autofill').addEventListener('click', event => action(event.currentTarget, async () => {
    if (!autoRun) return;
    try { await api('/api/autofill/apply', 'POST', {run_id: autoRun}); autoRun = null; await loadDay($('#autofill-date').value); await fetchData(); $('#autofill-results').innerHTML = '<p class="issue success">Предложенные уроки добавлены в расписания недели.</p>'; toast('Расписание недели сохранено'); }
    catch (reason) { autoRun = null; $('#autofill-error').textContent = reason.message; }
    finally { setTimeout(() => $('#apply-autofill').disabled = true, 0); }
  }));
  function clearImport() { importRun = null; $('#apply-import').disabled = true; $('#import-results').innerHTML = ''; $('#import-template').href = '/api/import/template?kind=' + $('#import-kind').value; }
  for (const id of ['import-kind', 'import-format', 'import-text']) $('#' + id).addEventListener('input', clearImport);
  $('#import-file').addEventListener('change', event => action(null, async () => { const file = event.target.files[0]; if (!file) return; if (file.size > 2_000_000) throw new Error('Файл должен быть меньше 2 МБ.'); $('#import-text').value = await file.text(); $('#import-format').value = file.name.toLowerCase().endsWith('.json') ? 'json' : 'csv'; clearImport(); }));
  $('#preview-import').addEventListener('click', event => action(event.currentTarget, async () => {
    clearImport(); $('#import-error').textContent = '';
    try { await saveDay(); const result = await api('/api/import/preview', 'POST', {kind: $('#import-kind').value, format: $('#import-format').value, text: $('#import-text').value}); importRun = result.run_id;
      const importKind = $('#import-kind').value;
      const heads = importKind === 'classes' ? ['Класс', 'Смена', 'Таблица печати'] : importKind === 'teachers' ? ['Учитель', 'Предметы', 'Классы', 'Кабинеты', 'Уроков в неделю'] : ['Класс', 'Предмет', 'Учитель', 'Уроков в неделю', 'Кабинеты'];
      const name = (items, id) => items.find(item => item.id === id)?.name || 'Не найдено';
      const rows = result.rows.map(row => importKind === 'classes' ? [row.name, row.shift, row.print_group || 'Автоматически'] : importKind === 'teachers' ? [row.name, row.subjects.join(', '), row.class_ids.map(id => name(state.classes, id)).join(', '), row.rooms.join(', '), row.weekly_hours ?? 'Без ограничения'] : [name(state.classes, row.class_id), name(data.catalog, row.subject_id), name(state.teachers, row.teacher_id), row.weekly_hours, row.room_ids.map(id => name(data.catalog, id)).join(', ') || 'Свободный']);
      $('#import-results').innerHTML = `<p class="workspace-result">Будет добавлено: ${result.added}. Совпадений пропущено: ${result.skipped.length}.</p><div class="workspace-table-scroll">${table(heads, rows.map(row => '<tr>' + row.map(cell).join('') + '</tr>'))}</div>`; $('#apply-import').disabled = !result.added;
    } catch (reason) { $('#import-error').textContent = reason.message; }
  }));
  $('#apply-import').addEventListener('click', event => action(event.currentTarget, async () => {
    try { if (!importRun) return; await api('/api/import/apply', 'POST', {run_id: importRun}); importRun = null; await updateAll(); $('#import-results').innerHTML = '<p class="issue success">Проверенные записи добавлены.</p>'; toast('Импорт завершён'); }
    catch (reason) { $('#import-error').textContent = reason.message; importRun = null; }
    finally { setTimeout(() => $('#apply-import').disabled = true, 0); }
  }));
  $('#logout-account').addEventListener('click', event => action(event.currentTarget, async () => { await saveDay(); await api('/api/account/logout', 'POST', {}); location.assign('/account'); }));
  $('#account-label').textContent = state.account ? state.account.name : 'Вход / регистрация';
  $('#logout-account').hidden = !state.account;
  return {open};
}
