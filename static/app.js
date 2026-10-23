'use strict';
import {clearPDF, loadPDF, renderPDF} from './pdf-preview.js';
import {workspaceTabs, workspaceHints, initializeWorkspace} from './workspace.js';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {classes: [], teachers: [], settings: {}, templates: [], day: null, date: '', revision: 0, shift: '1', dirty: false, generation: 0, issues: [], pdf: null, edit: null, constructorTeacher: null, teacherEdit: null, builder: null, builderTeacher: null, builderRequest: 0, builderTab: 'available'};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const clone = value => structuredClone(value);
let toastTimer, saveQueue = Promise.resolve(), savedSchedules = {days: [], weekly: []}, issueFocus = null;
const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
let referenceTemplate = null;
let workspace = null;
let lessonPicker = null, pickerRequest = 0;

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = 'visible' + (error ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.className = '', error ? 7000 : 3500);
}

async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: body === undefined ? {} : {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (response.status === 401) location.assign('/account');
  if (!response.ok) {
    const error = new Error(data.error || 'Не удалось выполнить запрос.');
    error.issues = data.issues;
    error.reasons = data.reasons;
    throw error;
  }
  return data;
}

async function action(button, fn) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try { await fn(); }
  catch (error) { toast(error.message, true); }
  finally { if (button) button.disabled = false; }
}

function invalidatePreview() {
  clearPDF();
  if (state.pdf) URL.revokeObjectURL(state.pdf);
  state.pdf = null;
  $('#pdf-preview').hidden = true;
  $('#preview-placeholder').hidden = false;
}

function setSaveStatus(message, kind) {
  $('#save-status').textContent = message;
  $('#save-status').dataset.kind = kind;
}

function setDirty() {
  clearIssueFocus();
  state.generation += 1;
  state.dirty = true;
  state.day.approved = false;
  setSaveStatus('Есть несохранённые изменения', 'dirty');
  $('#approval-status').textContent = 'Черновик';
  invalidatePreview();
}

async function saveDay(force = false) {
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    if (!state.day || (!state.dirty && (!force || state.revision > 0))) return;
    try {
      do {
        setSaveStatus('Сохранение…', 'saving');
        const generation = state.generation;
        const data = await api(`/api/day/${state.date}`, 'PUT', {day: clone(state.day), revision: state.revision});
        state.revision = data.revision;
        state.issues = data.issues;
        if (generation === state.generation) { state.day = data.day; state.dirty = false; }
      } while (state.dirty);
    } catch (error) { setSaveStatus('Не сохранено — повторите сохранение', 'error'); throw error; }
    setSaveStatus('Сохранено в базе', 'saved');
    renderSummary();
    renderIssues();
  });
  return saveQueue;
}

function shiftDate(value, offset) {
  const d = new Date(value + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}

function formatDate(value) {
  return new Date(value + 'T12:00:00Z').toLocaleDateString('ru-RU', {day:'numeric', month:'long', year:'numeric', timeZone:'UTC'});
}

async function loadDay(value) {
  const data = await api(`/api/day/${value}`);
  if (value !== state.date) $('#lesson-picker-dialog')?.close();
  clearIssueFocus();
  state.date = value;
  state.day = data.day;
  state.revision = data.revision;
  state.issues = data.issues;
  state.dirty = false;
  $('#day-date').value = value;
  $('#builder-date').value = value;
  $('#weekday').textContent = new Date(value + 'T12:00:00Z').toLocaleDateString('ru-RU', {weekday:'long', timeZone:'UTC'});
  $('#notes-1').value = state.day.notes['1'];
  $('#notes-2').value = state.day.notes['2'];
  $('#day-bells-hint').hidden = !Object.keys(state.day.bells || {}).length;
  renderPrintNotes();
  $('#export-date').textContent = 'Расписание на ' + formatDate(value);
  setSaveStatus(data.revision ? 'Сохранено в базе' : 'День ещё не заполнен', data.revision ? 'saved' : 'empty');
  renderHeading($('.nav-item.active')?.dataset.tab || 'schedule');
  invalidatePreview();
  renderGrid(); renderSummary(); renderIssues();
  fillBuilderSlots();
  if (!$('#constructor').hidden) await refreshBuilder();
}

async function changeDate(value) {
  if (!value || value === state.date) return;
  const old = state.date;
  $('#day-date').disabled = true;
  try { await saveDay(); await loadDay(value); }
  catch (error) { $('#day-date').value = old; throw error; }
  finally { $('#day-date').disabled = false; }
}

function renderSummary() {
  $('#class-count').textContent = state.classes.length;
  $('#class-nav-count').textContent = state.classes.length;
  $('#lesson-count').textContent = state.day.lessons.filter(l => !l.cancelled && l.groups.some(g => g.subject)).length;
  const errors = state.issues.filter(i => i.severity === 'error');
  $('#error-count').textContent = errors.length;
  $('#show-validation').classList.toggle('has-errors', errors.length > 0);
  $('#validation-summary-count').textContent = errors.length ? `Ошибок: ${errors.length}` : 'Пересечений нет';
  $('#validation-summary-count').classList.toggle('has-errors', errors.length > 0);
  $('#approval-status').textContent = state.day.approved ? 'Утверждено' : 'Черновик';
  for (const shift of [1, 2]) $('#shift-' + shift + '-count').textContent = state.classes.filter(c => c.shift === shift).length;
  renderTeachers();
}

function issueHTML(issues) {
  if (!issues.length) return '<div class="issue success">✓ Конфликтов не обнаружено. Расписание можно утвердить и скачать.</div>';
  const rows = values => values.map(i => {
    const location = {cells: i.cells || [], target: i.target, message: i.message};
    const linked = location.cells.length || location.target;
    const tag = linked ? 'button' : 'div';
    return `<${tag} class="issue ${i.severity === 'warning' ? 'warning' : ''} ${linked ? 'issue-link' : ''}" ${linked ? `type="button" data-issue-location="${escapeHTML(JSON.stringify(location))}" title="Показать место ошибки"` : ''}><span>${i.severity === 'warning' ? '△' : '!'} ${escapeHTML(i.message)}</span>${linked ? '<span class="issue-link-label" aria-hidden="true">Показать ↗</span>' : ''}</${tag}>`;
  }).join('');
  const warnings = issues.filter(i => i.severity === 'warning');
  if (warnings.length <= 10) return rows(issues);
  return rows(issues.filter(i => i.severity !== 'warning')) + `<details class="issue-details"><summary>Предупреждения: ${warnings.length} — показать список</summary><div class="issue-details-list">${rows(warnings)}</div></details>`;
}

function renderIssues() { $('#issues').innerHTML = issueHTML(state.issues); }

function clearIssueFocus() {
  issueFocus = null;
  const notice = $('#issue-focus');
  if (notice) notice.hidden = true;
  $$('.is-issue-target').forEach(element => {
    element.classList.remove('is-issue-target');
    element.removeAttribute('aria-describedby');
  });
  $$('.lesson-cell.has-issue').forEach(element => element.classList.remove('has-issue'));
}

function highlightIssueCells() {
  if (!issueFocus) return;
  $$('#grid-wrap [data-class]').forEach(button => {
    const matches = issueFocus.cells.some(cell => cell.class_id === Number(button.dataset.class) && cell.slot === Number(button.dataset.slot));
    button.classList.toggle('is-issue-target', matches);
    button.closest('.lesson-cell').classList.toggle('has-issue', matches);
    if (matches) button.setAttribute('aria-describedby', 'issue-focus-message');
  });
}

function revealIssueCell(cell) {
  let button = $(`#grid-wrap [data-class="${cell.class_id}"][data-slot="${cell.slot}"]`);
  if (!button) {
    const shifts = new Set(issueFocus.cells.map(target => String(state.classes.find(c => c.id === target.class_id).shift)));
    state.shift = shifts.size === 1 ? [...shifts][0] : 'all';
    $$('button[data-shift]').forEach(item => item.classList.toggle('active', item.dataset.shift === state.shift));
    $('#class-search').value = '';
    renderClassGroups();
    $('#class-group').value = 'all';
    renderGrid();
    button = $(`#grid-wrap [data-class="${cell.class_id}"][data-slot="${cell.slot}"]`);
  }
  if (!button) return;
  button.focus({preventScroll: true});
  // Centering keeps the cell clear of the sticky lesson numbers, time and header.
  button.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
  const wrap = $('#grid-wrap'), bounds = wrap.getBoundingClientRect(), cellBounds = button.closest('td').getBoundingClientRect();
  const stickyWidth = $('.schedule-table th.number-col').offsetWidth + $('.schedule-table th.time-col').offsetWidth;
  wrap.scrollLeft += cellBounds.left - bounds.left - stickyWidth - Math.max(0, (wrap.clientWidth - stickyWidth - cellBounds.width) / 2);
}

function showIssue(location) {
  clearIssueFocus();
  const cells = (Array.isArray(location.cells) ? location.cells : []).filter(cell =>
    Number.isInteger(cell.class_id) && Number.isInteger(cell.slot) && state.classes.some(c => c.id === cell.class_id) &&
    state.day.lessons.some(lesson => lesson.class_id === cell.class_id && lesson.slot === cell.slot));
  const unique = [...new Map(cells.map(cell => [`${cell.class_id}:${cell.slot}`, cell])).values()];
  if (!unique.length) {
    const target = ['rules', 'constructor', 'teachers'].includes(location.target) ? location.target : 'schedule';
    openTab(target);
    const section = target === 'rules' ? $('#rules-form') : target === 'constructor' ? $('#builder-class') : target === 'teachers' ? $('#teacher-list') : $('#grid-wrap');
    section.classList.add('is-issue-target');
    if (!section.matches('button,input,select,textarea,[tabindex]')) section.setAttribute('tabindex', '-1');
    section.focus({preventScroll: true});
    section.scrollIntoView({block: 'center', behavior: 'instant'});
    toast(location.message);
    return;
  }
  issueFocus = {cells: unique};
  const shifts = new Set(unique.map(cell => String(state.classes.find(c => c.id === cell.class_id).shift)));
  state.shift = shifts.size === 1 ? [...shifts][0] : 'all';
  $$('button[data-shift]').forEach(button => button.classList.toggle('active', button.dataset.shift === state.shift));
  $('#class-search').value = '';
  renderClassGroups();
  $('#class-group').value = 'all';
  openTab('schedule');
  renderGrid();
  $('#issue-focus-message').textContent = location.message;
  $('#issue-focus-targets').innerHTML = unique.map(cell => `<button type="button" class="button" data-issue-cell="${cell.class_id}:${cell.slot}">${escapeHTML(state.classes.find(c => c.id === cell.class_id).name)} · урок ${cell.slot}</button>`).join('');
  $('#issue-focus').hidden = false;
  revealIssueCell(unique[0]);
}

function renderPrintNotes() {
  renderExtrasCount();
  $('#print-notes-list').innerHTML = (state.day?.print_notes || []).map((note, index) => `<div class="print-note-row" data-print-note="${index}"><label>Перед таблицей с классом<select data-print-note-class>${state.classes.map(c => `<option value="${c.id}" ${c.id === note.class_id ? 'selected' : ''}>${escapeHTML(c.name)} · ${c.shift} смена</option>`).join('')}</select></label><label>Текст объявления<textarea data-print-note-text rows="2" maxlength="2000" placeholder="Например: английский язык — занятие всем классом">${escapeHTML(note.text)}</textarea></label><button type="button" class="button danger" data-remove-print-note="${index}" aria-label="Удалить объявление ${index + 1}">Удалить</button></div>`).join('');
}

function renderExtrasCount() {
  const count = (state.day?.print_notes || []).filter(note => note.text.trim()).length + Object.values(state.day?.notes || {}).filter(text => text.trim()).length;
  $('#schedule-extras-count').textContent = count ? String(count) : '';
}

function highlightCurrentCell() {
  const editing = $('#lesson-dialog').open && state.edit;
  const choosing = $('#lesson-picker-dialog')?.open && lessonPicker;
  const constructing = !$('#constructor').hidden;
  const cid = editing ? state.edit.cid : choosing ? lessonPicker.cid : constructing ? Number($('#builder-class').value) : null;
  const slot = editing ? Number($('#lesson-slot').value) : choosing ? lessonPicker.slot : constructing ? Number($('#builder-slot').value) : null;
  $$('#grid-wrap [data-class]').forEach(button => {
    const selected = Number(button.dataset.class) === cid && Number(button.dataset.slot) === slot;
    button.classList.toggle('is-editing', selected);
    button.closest('.lesson-cell').classList.toggle('is-editing', selected);
    if (selected) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });
  $$('#builder-class-day [data-builder-slot]').forEach(button => {
    const selected = state.builder?.class_info.id === cid && Number(button.dataset.builderSlot) === slot;
    button.classList.toggle('is-editing', selected);
    if (selected) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });
}

function renderGrid() {
  renderClassGroups();
  const query = normalizedSearch($('#class-search').value);
  const selectedGroup = $('#class-group').value;
  $('#class-group').disabled = Boolean(query);
  const classes = state.classes.filter(c => (state.shift === 'all' || String(c.shift) === state.shift) && normalizedSearch(c.name).includes(query) && (query || selectedGroup === 'all' || (c.print_group || '').trim() === selectedGroup.slice(2)));
  const total = state.classes.filter(c => state.shift === 'all' || String(c.shift) === state.shift).length;
  $('#visible-classes').textContent = `Показано: ${classes.length} · всего: ${total}`;
  if (!classes.length) { $('#grid-wrap').innerHTML = '<div class="empty-table">Классы не найдены. Измените поиск или назначение смен.</div>'; return; }
  const slots = [...new Set([...classes.flatMap(c => Object.keys(dayBells(c.shift)).map(Number)), ...state.day.lessons.filter(l => classes.some(c => c.id === l.class_id)).map(l => l.slot)])].sort((a, b) => a - b);
  const lookup = new Map(state.day.lessons.map(l => [`${l.class_id}:${l.slot}`, l]));
  const columnWidth = selectedGroup === 'all' && !query ? 164 : 120;
  let html = `<table class="schedule-table" style="width:${146 + classes.length * columnWidth}px"><thead><tr><th class="number-col" scope="col">№</th><th class="time-col" scope="col">Время</th>` + classes.map(c => `<th scope="col" style="width:${columnWidth}px">${escapeHTML(c.name)}${state.shift === 'all' ? `<br><small>${c.shift} смена</small>` : ''}</th>`).join('') + '</tr></thead><tbody>';
  for (const slot of slots) {
    const time = state.shift === 'all' ? 'По смене класса' : (dayBells(state.shift)[slot] || 'Не задано');
    html += `<tr><td class="number-col">${slot}</td><td class="time-col">${escapeHTML(time)}</td>`;
    for (const c of classes) {
      const lesson = lookup.get(`${c.id}:${slot}`);
      const unavailable = !dayBells(c.shift)[slot];
      let content = '+', cls = 'empty';
      if (lesson) {
        cls = lesson.cancelled ? 'cancelled' : '';
        content = lesson.cancelled ? 'Урок отменён' : lesson.groups.map((g, i) => `<div class="group-line">${g.rooms.length ? `<span class="room">${escapeHTML(g.rooms.join(','))}</span>` : ''}${lesson.groups.length > 1 ? `<small>${escapeHTML(g.label || 'Группа ' + (i+1))}: </small>` : ''}${escapeHTML(g.subject || 'Без предмета')}${g.teacher ? `<span class="teacher">${escapeHTML(g.teacher)}</span>` : ''}</div>`).join('');
        if (lesson.note) content += `<span class="cell-note">${escapeHTML(lesson.note)}</span>`;
      }
      html += `<td class="lesson-cell"><button class="cell-button ${cls}" data-class="${c.id}" data-slot="${slot}" ${unavailable && !lesson ? 'disabled' : ''} aria-label="${escapeHTML(c.name)}, урок ${slot}${lesson ? ', редактировать' : ', добавить'}">${content}</button></td>`;
    }
    html += '</tr>';
  }
  $('#grid-wrap').innerHTML = html + '</tbody></table>';
  $('#empty-hint').hidden = state.day.lessons.length > 0;
  highlightCurrentCell();
  highlightIssueCells();
}

function normalizedSearch(value) { return value.trim().toLocaleLowerCase('ru-RU'); }

function renderClassGroups() {
  const select = $('#class-group');
  const classes = state.classes.filter(c => state.shift === 'all' || String(c.shift) === state.shift);
  const groups = [...new Set(classes.map(c => (c.print_group || '').trim()).filter(Boolean))];
  const signature = JSON.stringify([state.shift, groups]);
  if (select.dataset.signature !== signature) {
    const previous = select.value, sameShift = select.dataset.shift === state.shift;
    select.innerHTML = groups.map(group => `<option value="g:${escapeHTML(group)}">${escapeHTML(group)}</option>`).join('') + '<option value="all">Все классы смены</option>';
    select.value = sameShift && [...select.options].some(option => option.value === previous) ? previous : state.shift !== 'all' && groups.length ? 'g:' + groups[0] : 'all';
    select.dataset.shift = state.shift;
    select.dataset.signature = signature;
  }
  select.closest('label').hidden = !groups.length;
}

function dayBells(shift) {
  return state.day?.bells?.[String(shift)] ?? Object.fromEntries(state.settings.bells[String(shift)].map((time, i) => [i + 1, time]));
}

function collectGroups() {
  return $$('.group-box').map(box => {
    const name = $('[data-field="teacher"]', box).value.trim();
    const teacher = findTeacher(name, box.dataset.teacherId);
    return {subject: $('[data-field="subject"]', box).value.trim(), teacher: teacher?.name || name,
            teacher_id: teacher?.id || null, label: $('[data-field="label"]', box).value.trim(),
            rooms: $('[data-field="rooms"]', box).value.split(/[,;]/).map(r => r.trim()).filter(Boolean)};
  });
}

function renderGroups(count, groups) {
  $('#group-fields').innerHTML = Array.from({length: count}, (_, i) => {
    const g = groups[i] || {subject:'',teacher:'',rooms:[],label:''};
    const teacher = findTeacher(g.teacher, g.teacher_id);
    const subjects = teacher?.subjects || [];
    return `<div class="group-box" data-teacher-id="${teacher?.id || ''}"><h3>${count > 1 ? 'Подгруппа ' + (i+1) : 'Весь класс'}</h3><div class="form-grid"><label class="wide">Предмет<input data-field="subject" list="group-subjects-${i}" maxlength="120" value="${escapeHTML(g.subject)}" placeholder="Название предмета"><datalist id="group-subjects-${i}">${subjects.map(s => `<option value="${escapeHTML(s)}"></option>`).join('')}</datalist></label><label>Преподаватель<input data-field="teacher" list="teacher-options" autocomplete="off" maxlength="120" value="${escapeHTML(teacher?.name || g.teacher)}" placeholder="Начните вводить ФИО"><small data-teacher-hint>${teacher ? 'Из каталога' + (teacher.active ? '' : ' · в архиве') : 'Выберите из подсказок каталога или введите ФИО'}</small></label><label>Кабинеты<input data-field="rooms" value="${escapeHTML(g.rooms.join(','))}" placeholder="18,25"></label><label class="wide" ${count === 1 ? 'hidden' : ''}>Название группы<input data-field="label" maxlength="40" value="${escapeHTML(g.label)}" placeholder="Например: группа 1"></label></div></div>`;
  }).join('');
}

function pickerLesson() {
  return state.day.lessons.find(lesson => lesson.class_id === lessonPicker.cid && lesson.slot === lessonPicker.slot);
}

function fillPickerGroups() {
  const groups = pickerLesson()?.groups || [];
  $('#lesson-picker-group-label').hidden = groups.length <= 1;
  lessonPicker.groupIndex = Math.min(lessonPicker.groupIndex, Math.max(0, groups.length - 1));
  $('#lesson-picker-group').innerHTML = (groups.length ? groups : [{label: 'Весь класс'}]).map((group, index) => `<option value="${index}">${escapeHTML(group.label || (groups.length > 1 ? 'Группа ' + (index + 1) : 'Весь класс'))}</option>`).join('');
  $('#lesson-picker-group').value = lessonPicker.groupIndex;
  const lesson = pickerLesson(), group = lesson?.groups[lessonPicker.groupIndex];
  $('#lesson-picker-current').textContent = group ? `${lesson.cancelled ? 'Урок отменён; сохранение возобновит занятие. ' : ''}Сейчас: ${group.subject || 'Предмет не указан'}${group.teacher ? ' · ' + group.teacher : ''}` : 'Выберите преподавателя и нажмите на его предмет в списке ниже.';
}

function openLessonPicker(cid, slot) {
  const cls = state.classes.find(c => c.id === cid);
  lessonPicker = {cid, slot, date: state.date, groupIndex: 0, context: null, choice: null, subject: '', loading: false, saving: false};
  $('#lesson-picker-title').textContent = `${cls.name} · урок ${slot}`;
  $('#lesson-picker-time').textContent = `${formatDate(state.date)} · ${dayBells(cls.shift)[slot] || 'Время не задано'} · ${cls.shift} смена`;
  $('#lesson-picker-unavailable').open = false;
  $('#lesson-picker-error').textContent = '';
  $('#lesson-picker-rooms').value = '';
  fillPickerGroups();
  $('#lesson-picker-dialog').showModal();
  $('#lesson-picker-title').focus();
  highlightCurrentCell();
  refreshLessonPicker();
}

async function refreshLessonPicker() {
  const picker = lessonPicker, request = ++pickerRequest;
  if (!picker || picker.saving) return;
  picker.context = null; picker.choice = null; picker.subject = ''; picker.loading = true;
  $('#lesson-picker-error').textContent = '';
  renderLessonPicker();
  try {
    await saveDay();
    if (request !== pickerRequest || lessonPicker !== picker) return;
    const params = new URLSearchParams({class_id: picker.cid, slot: picker.slot, group_index: picker.groupIndex});
    const context = await api(`/api/constructor/${picker.date}?${params}`);
    if (request !== pickerRequest || lessonPicker !== picker) return;
    if (context.revision !== state.revision) throw new Error('Расписание изменилось. Нажмите «Обновить список», чтобы загрузить актуальные уроки.');
    picker.context = context;
    const current = pickerLesson()?.groups[picker.groupIndex];
    const choice = context.candidates.find(c => c.available && c.teacher.class_ids.includes(picker.cid) &&
      (c.teacher.id === current?.teacher_id || normalizedSearch(c.teacher.name) === normalizedSearch(current?.teacher || '')) &&
      c.teacher.subjects.some(subject => normalizedSearch(subject) === normalizedSearch(current?.subject || '')));
    if (choice) {
      picker.choice = choice;
      picker.subject = choice.teacher.subjects.find(subject => normalizedSearch(subject) === normalizedSearch(current.subject));
      $('#lesson-picker-rooms').value = current.rooms.join(',');
    }
  } catch (error) {
    if (request === pickerRequest && lessonPicker === picker) $('#lesson-picker-error').textContent = error.message;
  } finally {
    if (request === pickerRequest && lessonPicker === picker) { picker.loading = false; renderLessonPicker(); }
  }
}

function renderLessonPicker() {
  const picker = lessonPicker;
  if (!picker) return;
  const candidates = (picker.context?.candidates || []).filter(c => c.teacher.class_ids.includes(picker.cid));
  const available = candidates.filter(c => c.available), unavailable = candidates.filter(c => !c.available);
  $('#lesson-picker-status').textContent = picker.loading ? 'Проверяем занятость, рабочие часы и нагрузку…' : picker.context ? (available.length ? `Доступно преподавателей: ${available.length}. Выберите предмет под фамилией.` : candidates.length ? 'Сейчас нет доступных преподавателей. Ниже указаны причины.' : 'В каталоге нет учителей, назначенных этому классу. Укажите рабочие классы в карточках преподавателей.') : 'Не удалось получить список преподавателей.';
  $('#lesson-picker-list').innerHTML = available.map(c => `<article class="picker-teacher ${picker.choice?.teacher.id === c.teacher.id ? 'selected' : ''}" data-picker-card="${c.teacher.id}"><h4>${escapeHTML(c.teacher.name)}</h4><p>Сегодня: ${c.day_count}${c.teacher.daily_limit === null ? '' : ' / ' + c.teacher.daily_limit} · Неделя: ${c.week_count}${c.teacher.weekly_hours === null ? '' : ' / ' + c.teacher.weekly_hours}</p><div class="picker-subjects">${c.teacher.subjects.map((subject, index) => `<button type="button" class="button" data-picker-teacher="${c.teacher.id}" data-picker-subject="${index}" aria-pressed="${picker.choice?.teacher.id === c.teacher.id && picker.subject === subject}" ${picker.saving ? 'disabled' : ''}>${escapeHTML(subject)}</button>`).join('')}</div></article>`).join('');
  $('#lesson-picker-unavailable').hidden = !unavailable.length;
  $('#lesson-picker-unavailable-title').textContent = `Недоступны в это время: ${unavailable.length} — показать причины`;
  $('#lesson-picker-unavailable-list').innerHTML = unavailable.map(c => `<article class="picker-teacher" data-picker-unavailable="${c.teacher.id}"><h4>${escapeHTML(c.teacher.name)}</h4><p>${escapeHTML(c.teacher.subjects.join(', ') || 'Предметы не заданы')}</p><ul>${c.reasons.map(reason => `<li>${escapeHTML(reason)}</li>`).join('')}</ul></article>`).join('');
  $('#lesson-picker-selection').hidden = !picker.choice;
  $('#lesson-picker-selected').textContent = picker.choice ? `${picker.subject} · ${picker.choice.teacher.name}` : '';
  $('#lesson-picker-room-hint').textContent = picker.choice?.busy_rooms.length ? `Кабинеты ${picker.choice.busy_rooms.join(', ')} заняты. Укажите свободный кабинет.` : 'Кабинеты подставлены из каталога; их можно изменить.';
  $('#lesson-picker-save').textContent = picker.saving ? 'Сохранение…' : picker.context?.occupied ? (pickerLesson()?.groups.length > 1 ? 'Заменить занятие группы' : 'Заменить урок') : 'Добавить урок';
  $('#lesson-picker-save').disabled = !picker.choice || picker.loading || picker.saving;
  $('#lesson-picker-refresh').disabled = picker.loading || picker.saving;
  $('#lesson-picker-group').disabled = picker.loading || picker.saving;
  $('#lesson-picker-rooms').disabled = picker.saving;
  $('#lesson-picker-details').disabled = picker.saving;
  $$('[data-close]', $('#lesson-picker-dialog')).forEach(button => button.disabled = picker.saving);
}

function selectPickerSubject(tid, index) {
  const picker = lessonPicker;
  if (!picker || picker.loading || picker.saving) return;
  const choice = picker.context?.candidates.find(c => c.teacher.id === tid && c.available && c.teacher.class_ids.includes(picker.cid));
  if (!choice || !choice.teacher.subjects[index]) return;
  if (picker.choice?.teacher.id !== tid) $('#lesson-picker-rooms').value = choice.free_rooms.join(',');
  picker.choice = choice; picker.subject = choice.teacher.subjects[index];
  $('#lesson-picker-error').textContent = '';
  renderLessonPicker();
  $(`[data-picker-teacher="${tid}"][data-picker-subject="${index}"]`)?.focus({preventScroll: true});
}

async function savePickerLesson() {
  const picker = lessonPicker;
  if (!picker?.choice || picker.loading || picker.saving) return;
  picker.saving = true;
  $('#lesson-picker-error').textContent = '';
  renderLessonPicker();
  try {
    const data = await api(`/api/constructor/${picker.date}`, 'POST', {class_id: picker.cid, slot: picker.slot,
      group_index: picker.groupIndex, teacher_id: picker.choice.teacher.id, subject: picker.subject,
      rooms: $('#lesson-picker-rooms').value.split(/[,;]/).map(value => value.trim()).filter(Boolean), revision: state.revision});
    state.day = data.day; state.revision = data.revision; state.issues = data.issues; state.dirty = false;
    clearIssueFocus(); invalidatePreview(); renderGrid(); renderSummary(); renderIssues();
    setSaveStatus('Сохранено в базе', 'saved');
    $('#lesson-picker-dialog').close();
    $(`#grid-wrap [data-class="${picker.cid}"][data-slot="${picker.slot}"]`)?.focus({preventScroll: true});
    toast('Урок сохранён. Рабочее время и нагрузка проверены.');
  } catch (error) {
    if (lessonPicker === picker) {
      $('#lesson-picker-error').textContent = [error.message, ...(error.reasons || []), ...(error.issues || []).map(issue => issue.message)].join(' ');
      picker.choice = null;
    }
  } finally {
    picker.saving = false;
    if (lessonPicker === picker) renderLessonPicker();
  }
}

function openLesson(cid, slot) {
  const cls = state.classes.find(c => c.id === cid);
  const lesson = state.day.lessons.find(l => l.class_id === cid && l.slot === slot);
  state.edit = {cid, slot};
  $('#lesson-title').textContent = `${cls.name} · урок ${slot}`;
  $('#lesson-error').textContent = '';
  let groups = lesson?.groups || [];
  const constructor = state.teachers.find(t => t.id === state.constructorTeacher && t.active);
  if (!lesson && constructor) groups = [{teacher_id: constructor.id, teacher: constructor.name,
    subject: constructor.subjects.length === 1 ? constructor.subjects[0] : '', rooms: constructor.rooms, label: ''}];
  $('#group-count').value = Math.max(1, groups.length);
  renderGroups(Math.max(1, groups.length), groups);
  $('#lesson-slot').innerHTML = Object.entries(dayBells(cls.shift)).map(([number, time]) => `<option value="${number}">${number} · ${escapeHTML(time)}</option>`).join('');
  if (!dayBells(cls.shift)[slot]) $('#lesson-slot').insertAdjacentHTML('beforeend', `<option value="${slot}">${slot} · время не задано</option>`);
  $('#lesson-slot').value = slot;
  $('#lesson-note').value = lesson?.note || '';
  $('#lesson-cancelled').checked = lesson?.cancelled || false;
  $('#delete-lesson').hidden = !lesson;
  $('#lesson-dialog').showModal();
  highlightCurrentCell();
  setTimeout(() => $('[data-field="subject"]')?.focus(), 0);
}

async function storeLesson(remove = false) {
  const {cid, slot} = state.edit;
  const before = clone(state.day);
  const target = Number($('#lesson-slot').value);
  if (!remove && !$('#lesson-cancelled').checked && !collectGroups().some(g => g.subject || g.teacher || g.rooms.length) && !$('#lesson-note').value.trim()) throw new Error('Введите предмет или отметьте отмену урока.');
  state.day.lessons = state.day.lessons.filter(l => !(l.class_id === cid && l.slot === slot));
  if (!remove) {
    const occupied = state.day.lessons.find(l => l.class_id === cid && l.slot === target);
    if (occupied && target !== slot) occupied.slot = slot;
    state.day.lessons.push({class_id:cid, slot:target, groups:collectGroups(), cancelled:$('#lesson-cancelled').checked, note:$('#lesson-note').value.trim()});
  }
  setDirty();
  try { await saveDay(); }
  catch (error) { state.day = before; state.dirty = true; throw error; }
  $('#lesson-dialog').close();
  renderGrid();
  if (!$('#constructor').hidden) await refreshBuilder();
  toast(remove ? 'Урок удалён' : 'Урок сохранён. Расписание проверено.');
}

const tabs = {
  ...workspaceTabs,
  schedule: ['Расписание дня', 'Уроки, замены и кабинеты — для обеих смен.'],
  documents: ['Документы и печать', 'Готовое расписание на бумаге и в редактируемых форматах.'],
  constructor: ['Конструктор расписания', 'Классы, свободные учителя, рабочее время и учебная нагрузка.'],
  teachers: ['Каталог учителей', 'Преподаватели, их предметы и кабинеты для конструктора расписания.'],
  classes: ['Классы и смены', 'Состав школы и порядок классов в расписании.'],
  templates: ['Шаблоны печати', 'Настройте оформление официальных документов.'],
};

const usageHints = {
  ...workspaceHints,
  schedule: ['Выберите дату, смену и группу классов. Поиск проверяет все классы выбранной смены. «Из шаблона» переносит сохранённое расписание, а сохранение в недельный шаблон находится в меню «Ещё».', 'Нажмите на ячейку: появятся учителя этого класса и кнопки с их предметами. Выберите предмет и сохраните урок. Подгруппы, примечания, отмена и перестановка доступны через «Подробное редактирование».', 'Нажмите на счётчик ошибок, затем на саму ошибку: связанные уроки выделятся красным. Кнопки с названиями классов переключают между ними.'],
  constructor: ['Выберите дату, класс и номер урока. Красная строка справа показывает выбранное время.', 'Выберите доступного учителя, предмет и кабинеты, затем нажмите «Добавить урок в расписание». Классы и рабочие часы учителя задаются в его карточке.', 'Вкладка «Ведёт урок» показывает, у каких классов заняты учителя. Переключатель «Показывать недоступных» объясняет ограничения.'],
  teachers: ['Нажмите «Добавить учителя», заполните ФИО, предметы и кабинеты.', 'Отметьте классы, в которых работает учитель, задайте нагрузку и доступность по дням недели. Новый класс сначала добавьте в разделе «Классы и смены».', 'Кнопка «В расписание» открывает конструктор. Стрелка ↗ редактирует карточку. «Классы и режим работы» раскрывает подробности, а в меню «Ещё» можно отправить учителя в архив или восстановить его.'],
  classes: ['Нажмите «Добавить класс», введите уникальное название и выберите смену. Кнопка «Добавить и сохранить» сохраняет новый класс и текущие изменения списка.', 'В поле «Таблица печати» задайте одинаковое название классам одного блока, например «Старшие» для 10-х и 11-х. Пустое поле включает автоматическую группировку по параллелям в оформлении по образцу.', 'Стрелки определяют порядок классов. Сохраните изменения. Чтобы назначать учителя новому классу, отметьте этот класс в карточке учителя в каталоге.'],
  documents: ['Выберите нужную дату в расписании, затем здесь укажите смену или отдельный класс.', 'Нажмите «Сформировать документ» и проверьте страницы PDF. Параметры оформления задаются в разделе «Шаблоны печати».', 'Выберите формат и скачайте файл. При критических ошибках доступен черновик; для официального документа исправьте ошибки.'],
  templates: ['Кнопка «Заполнить по образцу» выбирает компактные таблицы с серыми шапками и тонкой сеткой. После этого нажмите «Сохранить шаблон и звонки».', 'Состав таблиц задаётся в разделе «Классы и смены», а объявления перед отдельными таблицами — в расписании дня.', 'Настройте шапку утверждения, размеры и звонки. Для копирования самих уроков используйте кнопку «Из шаблона» в расписании дня.'],
};

function renderUsageHints() {
  const enabled = $('#show-hints').checked;
  document.body.classList.toggle('hints-off', !enabled);
  $('#usage-hints').hidden = !enabled;
  const tab = $('.nav-item.active')?.dataset.tab || 'schedule';
  $('#usage-hints-title').textContent = 'Подсказки: ' + tabs[tab][0].toLocaleLowerCase('ru-RU');
  $('#usage-hints-steps').innerHTML = usageHints[tab].map(text => `<li>${escapeHTML(text)}</li>`).join('');
}

function renderHeading(tab) {
  const titles = {
    schedule: 'Всё по <em>расписанию.</em>',
    teachers: 'За каждым уроком — <em>учитель.</em>',
    constructor: 'У каждого урока <em>своё место.</em>',
    classes: 'Все классы. <em>Одна школа.</em>',
    documents: 'Хорошо на экране. <em>Чётко на бумаге.</em>',
  };
  const title = tabs[tab] || tabs.schedule;
  if (titles[tab]) $('#page-title').innerHTML = titles[tab];
  else $('#page-title').textContent = title[0];
  const descriptions = {schedule:'Учебный день школы — в одной понятной таблице.', teachers:'Один каталог для всех классов и предметов.'};
  $('#page-description').textContent = descriptions[tab] || title[1];
  const formatted = state.date ? formatDate(state.date).replace(/\s*г\.$/, '') : '';
  $('.heading .eyebrow').textContent = tab === 'schedule' && formatted ? `${$('#weekday').textContent} · ${formatted}`.toLocaleUpperCase('ru-RU') : 'ШКОЛЬНЫЙ КАБИНЕТ / ' + title[0].toLocaleUpperCase('ru-RU');
}

function openTab(tab, refresh = true) {
  if (!tabs[tab]) tab = 'schedule';
  $$('.tab-panel').forEach(panel => panel.hidden = panel.id !== tab);
  $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  $$('.nav-group').forEach(group => group.open = Boolean($('.nav-item.active', group)));
  $$('.nav-item').forEach(button => button.dataset.tab === tab ? button.setAttribute('aria-current', 'page') : button.removeAttribute('aria-current'));
  renderHeading(tab);
  $('#breadcrumb').textContent = tabs[tab][0];
  $('.heading-actions').hidden = tab !== 'schedule';
  if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab);
  renderUsageHints();
  if (tab === 'constructor' && state.day && refresh) action(null, refreshBuilder);
  if (workspace && workspaceTabs[tab]) action(null, () => workspace.open(tab));
  highlightCurrentCell();
  window.scrollTo({top: 0, behavior: 'instant'});
}

let classDraft = [];
function renderClasses() {
  $('#classes-total').textContent = `(${classDraft.length})`;
  $('#class-list').innerHTML = classDraft.map((c, i) => `<div class="class-row" data-index="${i}"><span class="class-position">${String(i+1).padStart(2, '0')}</span><input value="${escapeHTML(c.name)}" maxlength="40" aria-label="Название класса ${i+1}"><input class="class-print-group" data-print-group value="${escapeHTML(c.print_group || '')}" maxlength="60" placeholder="Таблица (авто)" aria-label="Таблица печати для класса ${escapeHTML(c.name)}"><select aria-label="Смена класса ${escapeHTML(c.name)}"><option value="1" ${c.shift === 1 ? 'selected' : ''}>1 смена</option><option value="2" ${c.shift === 2 ? 'selected' : ''}>2 смена</option></select><button class="order-button" data-move="-1" ${i === 0 ? 'disabled' : ''} aria-label="Поднять ${escapeHTML(c.name)}">↑</button><button class="order-button" data-move="1" ${i === classDraft.length-1 ? 'disabled' : ''} aria-label="Опустить ${escapeHTML(c.name)}">↓</button></div>`).join('');
  let previous = '';
  $$('.class-row').forEach(row => {
    const group = (classDraft[Number(row.dataset.index)].print_group || '').trim();
    if (group && group !== previous) row.insertAdjacentHTML('beforebegin', `<h3 class="class-list-group">${escapeHTML(group)}</h3>`);
    previous = group;
  });
}

function collectClassDraft() {
  $$('.class-row').forEach(row => {
    const c = classDraft[Number(row.dataset.index)];
    c.name = $('input', row).value.trim();
    c.shift = Number($('select', row).value);
    c.print_group = $('[data-print-group]', row).value.trim();
  });
}

async function storeClasses(extra = null) {
  collectClassDraft();
  const classes = extra ? [...classDraft, extra] : classDraft;
  await saveDay();
  await api('/api/classes', 'PUT', {classes});
  await refreshState();
  await loadDay(state.date);
}

function scheduleSummary(item) {
  return `Уроков: ${item.lesson_count} · классов: ${item.class_count}`;
}

async function openSchedulePicker() {
  await saveDay();
  savedSchedules = await api('/api/schedules');
  const days = savedSchedules.days.filter(item => item.date !== state.date);
  $('#copy-saved-date').innerHTML = days.length ? days.map(item => `<option value="${item.date}">${escapeHTML(formatDate(item.date))} — ${escapeHTML(scheduleSummary(item))}</option>`).join('') : '<option value="">Сохранённых расписаний других дней пока нет</option>';
  $('#copy-weekday').innerHTML = savedSchedules.weekly.length ? savedSchedules.weekly.map(item => `<option value="${item.weekday}">${weekdays[item.weekday]} — ${escapeHTML(scheduleSummary(item))}</option>`).join('') : '<option value="">Недельные шаблоны пока не сохранены</option>';
  const weekday = (new Date(state.date + 'T12:00:00Z').getUTCDay() + 6) % 7;
  if (savedSchedules.weekly.some(item => item.weekday === weekday)) $('#copy-weekday').value = weekday;
  $('#copy-source').value = days.length ? 'saved' : savedSchedules.weekly.length ? 'weekly' : 'saved';
  $('#copy-source-date').value = shiftDate(state.date, -1);
  $('#copy-target').textContent = 'Применить к дате: ' + formatDate(state.date) + `. Будет заменено записей: ${state.day.lessons.length}.`;
  $('#copy-error').textContent = '';
  updateSchedulePicker();
  $('#copy-dialog').showModal();
}

function selectedSchedule() {
  const source = $('#copy-source').value;
  if (source === 'weekly') return savedSchedules.weekly.find(item => String(item.weekday) === $('#copy-weekday').value);
  const value = source === 'saved' ? $('#copy-saved-date').value : $('#copy-source-date').value;
  return savedSchedules.days.find(item => item.date === value && item.date !== state.date);
}

function updateSchedulePicker() {
  const source = $('#copy-source').value;
  $('#copy-saved-label').hidden = source !== 'saved';
  $('#copy-weekday-label').hidden = source !== 'weekly';
  $('#copy-date-label').hidden = source !== 'date';
  $('#copy-source-date').required = source === 'date';
  const item = selectedSchedule();
  $('#apply-schedule-template').disabled = !item;
  $('#copy-summary').textContent = item ? scheduleSummary(item) + '. Уроки и примечания будут перенесены, утверждение потребуется заново.' : source === 'weekly' ? 'Сначала сохраните расписание кнопкой «Сохранить в недельный шаблон».' : 'Выберите другую дату с сохранённым расписанием. Для создания источника заполните день и нажмите «Сохранить день».';
}

async function applyScheduleTemplate() {
  const item = selectedSchedule();
  if (!item) throw new Error('Выберите сохранённое расписание.');
  await saveDay();
  const source = $('#copy-source').value;
  const payload = source === 'weekly' ? {source, source_weekday: item.weekday} : {source, source_date: item.date, source_revision: item.revision};
  await api(`/api/day/${state.date}/copy`, 'POST', {...payload, revision: state.revision});
  await loadDay(state.date);
  $('#copy-dialog').close();
  toast('Шаблон применён. Расписание на выбранную дату проверено.');
}

function fillTemplate(template) {
  const form = $('#template-form');
  for (const [key, value] of Object.entries(template)) {
    const input = form.elements.namedItem(key);
    if (input) { if (input.type === 'checkbox') input.checked = value; else input.value = value; }
  }
}

function renderSettings() {
  const builderClass = $('#builder-class').value;
  $('#builder-class').innerHTML = state.classes.map(c => `<option value="${c.id}">${escapeHTML(c.name)} · ${c.shift} смена</option>`).join('');
  if (state.classes.some(c => String(c.id) === builderClass)) $('#builder-class').value = builderClass;
  fillBuilderSlots();
  const selectedClass = $('#export-class').value;
  $('#export-class').innerHTML = state.classes.map(c => `<option value="${c.id}">${escapeHTML(c.name)} · ${c.shift} смена</option>`).join('');
  if (state.classes.some(c => String(c.id) === selectedClass)) $('#export-class').value = selectedClass;
  $('#template-preset').innerHTML = state.templates.map((t, i) => `<option value="${i}" ${t.name === state.settings.template.name ? 'selected' : ''}>${escapeHTML(t.name)}</option>`).join('');
  fillTemplate(state.settings.template);
  updateExportOptions();
  for (const s of [1, 2]) $('#bells-' + s).value = state.settings.bells[String(s)].join('\n');
  classDraft = clone(state.classes);
  renderClasses();
}

async function refreshState() {
  const data = await api('/api/state');
  state.classes = data.classes;
  state.settings = data.settings;
  state.templates = data.templates;
  state.teachers = data.teachers || [];
  referenceTemplate = data.reference_template;
  renderSettings();
}

function teacherNameKey(value) { return String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru-RU'); }

function findTeacher(name, selectedId) {
  return state.teachers.find(t => teacherNameKey(t.name) === teacherNameKey(name) && (t.active || String(t.id) === String(selectedId)));
}

function updateGroupTeacher(box) {
  const input = $('[data-field="teacher"]', box);
  const teacher = findTeacher(input.value, box.dataset.teacherId);
  box.dataset.teacherId = teacher?.id || '';
  $('[data-teacher-hint]', box).textContent = teacher ? 'Из каталога' + (teacher.active ? '' : ' · в архиве') : 'Выберите из подсказок каталога или введите ФИО';
  const subject = $('[data-field="subject"]', box);
  const list = $('#' + subject.getAttribute('list'));
  list.innerHTML = (teacher?.subjects || []).map(s => `<option value="${escapeHTML(s)}"></option>`).join('');
  if (teacher) {
    input.value = teacher.name;
    if (!subject.value.trim() && teacher.subjects.length === 1) subject.value = teacher.subjects[0];
    const rooms = $('[data-field="rooms"]', box);
    if (!rooms.value.trim()) rooms.value = teacher.rooms.join(',');
  }
}

function renderTeachers() {
  const active = state.teachers.filter(t => t.active);
  $('#teacher-nav-count').textContent = active.length;
  $('#teacher-options').innerHTML = active.map(t => `<option value="${escapeHTML(t.name)}">${escapeHTML(t.subjects.join(', '))}</option>`).join('');
  $('#catalog-date').textContent = state.date ? formatDate(state.date) : '';
  const query = normalizedSearch($('#teacher-search').value);
  const filter = $('#teacher-filter').value;
  const teachers = state.teachers.filter(t => (filter === 'all' || (filter === 'active' ? t.active : !t.active)) && normalizedSearch(t.name + ' ' + t.subjects.join(' ')).includes(query));
  $('#teacher-list').innerHTML = teachers.length ? teachers.map((t, index) => {
    const count = (state.day?.lessons || []).filter(l => !l.cancelled).reduce((n, l) => n + l.groups.filter(g => g.teacher_id === t.id || (!g.teacher_id && teacherNameKey(g.teacher) === teacherNameKey(t.name))).length, 0);
    const classes = state.classes.filter(c => t.class_ids?.includes(c.id)).map(c => c.name);
    const initials = t.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toLocaleUpperCase('ru-RU');
    const workload = t.weekly_hours == null ? 'Нагрузка не задана' : t.weekly_hours + ' уроков в неделю';
    const summary = 'Классы: ' + (classes.join(', ') || 'пока не назначены') + '. Нагрузка: ' + (t.weekly_hours == null ? 'не задана' : t.weekly_hours + ' уроков/нед.') + '. Часы работы: ' + (t.availability_enabled ? 'по дням недели' : 'ограничение не включено') + '.';
    return `<article class="teacher-card" data-teacher-card="${t.id}"><div class="teacher-card-heading"><span class="teacher-avatar" aria-hidden="true">${escapeHTML(initials)}</span><span class="teacher-card-number">${t.active ? String(index + 1).padStart(2, '0') : 'В архиве'}</span></div><h3>${escapeHTML(t.name)}</h3><div class="teacher-subjects">${t.subjects.length ? t.subjects.map(s => `<span>${escapeHTML(s)}</span>`).join('') : '<span class="muted">Предметы пока не указаны</span>'}</div><div class="teacher-class-pills">${classes.slice(0, 6).map(name => `<span>${escapeHTML(name)}</span>`).join('')}${classes.length > 6 ? `<span>+${classes.length - 6}</span>` : ''}${!classes.length ? '<span>Классы не назначены</span>' : ''}</div><div class="teacher-card-foot"><span>${escapeHTML(workload)}</span><button class="teacher-edit-link" data-teacher-edit="${t.id}" aria-label="Изменить ${escapeHTML(t.name)}">↗</button></div><div class="teacher-card-actions">${t.active ? `<button class="button primary" data-teacher-use="${t.id}" aria-label="Использовать ${escapeHTML(t.name)} в расписании">В расписание</button>` : '<span class="small">Учитель в архиве</span>'}<details class="teacher-overflow"><summary aria-label="Действия с учителем ${escapeHTML(t.name)}">Ещё ···</summary><div><button class="button" data-teacher-archive="${t.id}">${t.active ? 'В архив' : 'Восстановить'}</button></div></details></div><details class="teacher-details"><summary>Классы и режим работы</summary><p class="teacher-work-summary">${escapeHTML(summary)}</p><p class="teacher-day-count">Занятий на выбранную дату: ${count}</p><p class="teacher-rooms">Кабинеты: <strong>${escapeHTML(t.rooms.join(', ') || 'не заданы')}</strong></p>${t.note ? `<p class="teacher-note">${escapeHTML(t.note)}</p>` : ''}</details></article>`;
  }).join('') : `<div class="empty-table">${state.teachers.length ? 'Учителя не найдены. Измените поиск или фильтр.' : 'В каталоге пока нет учителей. Нажмите «Добавить учителя» и заполните первую карточку.'}</div>`;
  const constructor = state.teachers.find(t => t.id === state.constructorTeacher && t.active);
  $('#constructor-banner').hidden = !constructor;
  if (constructor) {
    $('#constructor-name').textContent = 'Конструктор: ' + constructor.name;
    $('#constructor-description').textContent = (constructor.subjects.join(', ') || 'Предмет можно указать в уроке') + '. Нажмите свободную ячейку расписания.';
  }
}

function openTeacherEditor(id = null) {
  const teacher = state.teachers.find(t => t.id === id);
  state.teacherEdit = teacher ? clone(teacher) : null;
  $('#teacher-dialog-title').textContent = teacher ? 'Изменить учителя' : 'Добавить учителя';
  $('#teacher-name').value = teacher?.name || '';
  $('#teacher-subjects').value = (teacher?.subjects || []).join('\n');
  $('#teacher-rooms').value = (teacher?.rooms || []).join(',');
  $('#teacher-note').value = teacher?.note || '';
  $('#teacher-error').textContent = '';
  $('#teacher-weekly-hours').value = teacher?.weekly_hours ?? '';
  $('#teacher-daily-limit').value = teacher?.daily_limit ?? '';
  $('#teacher-class-search').value = '';
  $('#teacher-class-fields').innerHTML = state.classes.map(c => `<div class="teacher-class-choice" data-class-choice="${c.id}"><label class="checkbox"><input type="checkbox" data-teacher-class="${c.id}" ${(teacher?.class_ids || []).includes(c.id) ? 'checked' : ''}> ${escapeHTML(c.name)} <small>${c.shift} смена</small></label><input type="number" min="0" max="84" data-class-hours="${c.id}" value="${teacher?.class_hours?.[String(c.id)] ?? ''}" placeholder="уроков/нед." aria-label="Уроков в неделю в ${escapeHTML(c.name)}" ${(teacher?.class_ids || []).includes(c.id) ? '' : 'disabled'}></div>`).join('');
  $('#teacher-availability-enabled').checked = teacher ? teacher.availability_enabled : true;
  const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
  $('#teacher-availability-fields').innerHTML = weekdays.map((name, i) => `<label class="availability-row"><span>${name}</span><input data-availability="${i}" value="${escapeHTML(teacher ? (teacher.availability?.[String(i)] || []).join('; ') : (i < 5 ? '08:00–18:00' : ''))}" placeholder="Выходной или 08:00–13:00; 14:00–17:00"></label>`).join('');
  toggleAvailability();
  $('#teacher-dialog').showModal();
  $('#teacher-name').focus();
}

async function refreshTeacherCatalog() {
  const data = await api('/api/state');
  state.teachers = data.teachers || [];
  await loadDay(state.date);
}

async function storeTeacher() {
  await saveDay();
  const value = {name: $('#teacher-name').value, subjects: $('#teacher-subjects').value,
    rooms: $('#teacher-rooms').value, note: $('#teacher-note').value,
    active: state.teacherEdit?.active ?? true, revision: state.teacherEdit?.revision};
  value.class_ids = $$('[data-teacher-class]:checked').map(input => Number(input.dataset.teacherClass));
  value.class_hours = Object.fromEntries(value.class_ids.map(cid => [String(cid), $(`[data-class-hours="${cid}"]`).value]).filter(([, hours]) => hours !== '').map(([cid, hours]) => [cid, Number(hours)]));
  value.weekly_hours = $('#teacher-weekly-hours').value === '' ? null : Number($('#teacher-weekly-hours').value);
  value.daily_limit = $('#teacher-daily-limit').value === '' ? null : Number($('#teacher-daily-limit').value);
  value.availability_enabled = $('#teacher-availability-enabled').checked;
  value.availability = Object.fromEntries($$('[data-availability]').map(input => [input.dataset.availability, input.value]));
  await api(state.teacherEdit ? `/api/teachers/${state.teacherEdit.id}` : '/api/teachers', state.teacherEdit ? 'PUT' : 'POST', value);
  await refreshTeacherCatalog();
  $('#teacher-dialog').close();
  toast('Карточка учителя сохранена');
}

function toggleAvailability() {
  $$('[data-availability]').forEach(input => input.disabled = !$('#teacher-availability-enabled').checked);
}

function fillBuilderSlots() {
  const cls = state.classes.find(c => String(c.id) === $('#builder-class').value);
  if (!cls) return;
  const selected = $('#builder-slot').value;
  $('#builder-slot').innerHTML = Object.entries(dayBells(cls.shift)).map(([number, time]) => `<option value="${number}">Урок ${number} · ${escapeHTML(time)}</option>`).join('');
  if (dayBells(cls.shift)[selected]) $('#builder-slot').value = selected;
}

async function refreshBuilder() {
  if (!state.classes.length) { $('#builder-summary').textContent = 'Сначала добавьте классы в справочник.'; $('#builder-teachers').innerHTML = ''; $('#builder-class-day').innerHTML = ''; $('#builder-add').disabled = true; return; }
  state.builder = null;
  $('#builder-add').disabled = true;
  $$('[data-builder-select]').forEach(button => button.disabled = true);
  highlightCurrentCell();
  await saveDay();
  const request = ++state.builderRequest;
  const params = new URLSearchParams({class_id: $('#builder-class').value, slot: $('#builder-slot').value});
  $('#builder-summary').textContent = 'Проверяем рабочее время и нагрузку…';
  const context = await api(`/api/constructor/${state.date}?${params}`);
  if (request !== state.builderRequest) return;
  state.builder = context;
  if (!context.candidates.some(c => c.teacher.id === state.builderTeacher && c.available)) state.builderTeacher = null;
  renderBuilder();
}

function renderBuilder() {
  const context = state.builder;
  if (!context) return;
  const available = context.candidates.filter(c => c.available);
  $('#builder-week').textContent = `Неделя ${formatDate(context.week_start)} — ${formatDate(context.week_end)}. Учитываются сохранённые даты, а для остальных дней — недельные шаблоны.`;
  $('#builder-summary').textContent = context.occupied ? 'Этот урок уже заполнен. Выберите свободный номер или измените занятие ниже.' : `Свободных преподавателей: ${available.length}. ${context.class_info.name}, урок ${context.slot} (${context.time}).`;
  const choices = context.candidates.filter(c => c.available || $('#builder-show-unavailable').checked);
  $('#builder-teachers').innerHTML = choices.length ? choices.map(c => `<article class="builder-teacher ${c.available ? '' : 'unavailable'} ${state.builderTeacher === c.teacher.id ? 'selected' : ''}" data-builder-card="${c.teacher.id}"><div><h3>${escapeHTML(c.teacher.name)}</h3><p>${escapeHTML(c.teacher.subjects.join(', ') || 'Предметы не заданы')}</p><div class="workload-badges"><span>Неделя: ${c.week_count}${c.teacher.weekly_hours === null ? '' : ' / ' + c.teacher.weekly_hours}</span><span>Класс: ${c.class_week_count}${c.class_target === null ? '' : ' / ' + c.class_target}</span><span>Сегодня: ${c.day_count}${c.teacher.daily_limit === null ? '' : ' / ' + c.teacher.daily_limit}</span></div>${c.reasons.length ? `<ul>${c.reasons.map(r => `<li>${escapeHTML(r)}</li>`).join('')}</ul>` : '<small class="builder-available">Свободен в это время</small>'}</div><button class="button ${c.available ? 'primary' : ''}" data-builder-select="${c.teacher.id}" ${!c.available || context.occupied ? 'disabled' : ''}>${state.builderTeacher === c.teacher.id ? 'Выбран' : 'Выбрать'}</button></article>`).join('') : '<div class="empty-table">Подходящих учителей нет. Включите показ недоступных, чтобы увидеть причины, или настройте классы и часы в каталоге.</div>';
  const busy = context.busy_teachers || [];
  $('#builder-available-count').textContent = available.length;
  $('#builder-busy-count').textContent = busy.length;
  $('#builder-busy-teachers').innerHTML = busy.length ? busy.map(c => `<article class="builder-busy-teacher" data-busy-teacher="${c.teacher.id}"><div class="busy-teacher-heading"><h3>${escapeHTML(c.teacher.name)}</h3><span>Ведёт урок${c.teacher.active ? '' : ' · в архиве'}</span></div>${c.busy_lessons.map(l => `<div class="busy-lesson"><strong>${escapeHTML(l.class_name)}</strong><p>${escapeHTML(l.subject)}${l.group ? ' · ' + escapeHTML(l.group) : ''}</p><small>Урок ${l.slot} · ${escapeHTML(l.time)} · ${l.shift} смена${l.rooms.length ? ' · Кабинеты: ' + escapeHTML(l.rooms.join(', ')) : ''}</small></div>`).join('')}</article>`).join('') : '<div class="empty-table">В выбранное время никто из каталога не ведёт урок.</div>';
  renderBuilderTabs();
  const choice = context.candidates.find(c => c.teacher.id === state.builderTeacher && c.available);
  $('#builder-selection').textContent = choice ? choice.teacher.name : 'Выберите преподавателя из списка.';
  $('#builder-subject').disabled = !choice;
  $('#builder-rooms').disabled = !choice;
  $('#builder-add').disabled = !choice || context.occupied;
  if (!choice) { $('#builder-subject').innerHTML = ''; $('#builder-rooms').value = ''; $('#builder-room-hint').textContent = ''; }
  const lessons = new Map(state.day.lessons.filter(l => l.class_id === context.class_info.id).map(l => [l.slot, l]));
  $('#builder-class-day').innerHTML = Object.entries(dayBells(context.class_info.shift)).map(([number, time]) => {
    const slot = Number(number), lesson = lessons.get(slot);
    return `<button class="builder-day-row ${slot === context.slot ? 'active' : ''}" data-builder-slot="${slot}"><span class="builder-day-time">${slot}<small>${escapeHTML(time)}</small></span><span class="builder-day-content"><strong>${lesson ? escapeHTML(lesson.cancelled ? 'Урок отменён' : lesson.groups.map(g => g.subject + ' · ' + g.teacher).join('; ')) : 'Свободно'}</strong>${lesson ? '<small>Изменить</small>' : ''}</span></button>`;
  }).join('');
  highlightCurrentCell();
}

function renderBuilderTabs() {
  const busy = state.builderTab === 'busy';
  $('#builder-teachers').hidden = busy;
  $('#builder-busy-teachers').hidden = !busy;
  $$('[data-builder-tab]').forEach(button => {
    const active = button.dataset.builderTab === state.builderTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
}

function selectBuilderTeacher(id) {
  const choice = state.builder?.candidates.find(c => c.teacher.id === id && c.available);
  if (!choice || state.builder.occupied) return;
  state.builderTeacher = id;
  renderBuilder();
  $('#builder-subject').innerHTML = choice.teacher.subjects.map(subject => `<option>${escapeHTML(subject)}</option>`).join('');
  $('#builder-rooms').value = choice.free_rooms.join(',');
  $('#builder-room-hint').textContent = choice.busy_rooms.length ? `Кабинеты ${choice.busy_rooms.join(', ')} заняты. Выберите другой кабинет.` : 'Кабинеты можно изменить для этого урока.';
  $('#builder-error').textContent = '';
}

async function assignBuilderLesson() {
  if (!state.builder || !state.builderTeacher) throw new Error('Выберите доступного учителя.');
  const data = await api(`/api/constructor/${state.date}`, 'POST', {class_id: state.builder.class_info.id,
    slot: state.builder.slot, teacher_id: state.builderTeacher, subject: $('#builder-subject').value,
    rooms: $('#builder-rooms').value.split(/[,;]/).map(v => v.trim()).filter(Boolean), revision: state.builder.revision});
  state.day = data.day; state.revision = data.revision; state.issues = data.issues; state.dirty = false;
  setSaveStatus('Сохранено в базе', 'saved');
  invalidatePreview(); renderGrid(); renderSummary(); renderIssues();
  state.builderTeacher = null;
  toast('Урок добавлен. Рабочее время и нагрузка проверены.');
  await refreshBuilder();
}

function exportURL(fmt, scope, inline = false) {
  const params = new URLSearchParams({scope, draft: $('#draft-export').checked ? '1' : '0', class_id: $('#export-class').value, inline: inline ? '1' : '0'});
  return `/api/export/${state.date}/${fmt}?${params}`;
}

function updateExportOptions() {
  const scope = $('#preview-scope');
  const selectedClass = scope.value === 'class';
  $('#export-class').closest('label').hidden = !selectedClass;
  $('#download-scope').textContent = selectedClass ? $('#export-class').selectedOptions[0]?.textContent || 'Выберите класс' : scope.selectedOptions[0].textContent;
  const formats = {pdf: 'PDF', xlsx: 'Excel', docx: 'Word'};
  $('[data-download="selected"]').textContent = '↓ Скачать ' + formats[$('#export-format').value];
}

async function getFile(fmt, scope) {
  const response = await fetch(exportURL(fmt, scope, fmt === 'pdf'));
  if (!response.ok) {
    const data = await response.json();
    if (data.issues) $('#export-issues').innerHTML = issueHTML(data.issues);
    throw new Error(data.error || 'Не удалось сформировать документ.');
  }
  $('#export-issues').innerHTML = '';
  return response.blob();
}

async function updatePDF() {
  const input = $('#preview-page');
  const number = Math.min(Number(input.max) || 1, Math.max(1, Number(input.value) || 1));
  input.value = number;
  if (state.pdf) await renderPDF(number, $('#preview-zoom').value);
}

async function preview(scope = $('#preview-scope').value) {
  await saveDay();
  const blob = await getFile('pdf', scope);
  invalidatePreview();
  state.pdf = URL.createObjectURL(blob);
  $('#preview-scope').value = scope === 'all' ? 'both' : scope;
  updateExportOptions();
  $('#preview-page').value = 1;
  $('#pdf-preview').hidden = false;
  $('#preview-placeholder').hidden = true;
  const count = await loadPDF(blob);
  $('#preview-page').max = count;
  $('#preview-page-count').textContent = 'из ' + count;
  await updatePDF();
  return blob;
}

async function download(scope) {
  // A real, current PDF is displayed before each download, including Office exports.
  const pdf = await preview(scope);
  const format = $('#export-format').value;
  const blob = format === 'pdf' ? pdf : await getFile(format, scope);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `timetable_${state.date}_${scope}.${format}`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  toast('Файл сформирован из сохранённого расписания');
}

function bindEvents() {
  $('#show-hints').addEventListener('change', () => {
    try { localStorage.setItem('timetable.showHints', String($('#show-hints').checked)); } catch (_) { /* The switch still works when browser storage is unavailable. */ }
    renderUsageHints();
  });
  $$('.nav-item').forEach(b => b.addEventListener('click', () => openTab(b.dataset.tab)));
  $$('[data-open]').forEach(b => b.addEventListener('click', () => openTab(b.dataset.open)));
  window.addEventListener('hashchange', () => openTab(location.hash.slice(1)));
  $('[href="#schedule"]').addEventListener('click', () => openTab('schedule'));
  $('#save-day').addEventListener('click', e => action(e.currentTarget, async () => { await saveDay(true); toast('Расписание дня сохранено'); }));
  $('#show-validation').addEventListener('click', () => { $('#schedule-validation').open = true; $('#schedule-validation').scrollIntoView({behavior: 'smooth', block: 'start'}); });
  for (const selector of ['#issues', '#export-issues']) $(selector).addEventListener('click', event => {
    const button = event.target.closest('[data-issue-location]');
    if (button) showIssue(JSON.parse(button.dataset.issueLocation));
  });
  $('#clear-issue-focus').addEventListener('click', () => { clearIssueFocus(); $('#show-validation').focus({preventScroll: true}); });
  $('#issue-focus-targets').addEventListener('click', event => {
    const button = event.target.closest('[data-issue-cell]');
    if (!button || !issueFocus) return;
    const [cid, slot] = button.dataset.issueCell.split(':').map(Number);
    const cell = issueFocus.cells.find(cell => cell.class_id === cid && cell.slot === slot);
    if (cell) { openTab('schedule'); revealIssueCell(cell); }
  });
  $$('.action-menu button').forEach(button => button.addEventListener('click', () => button.closest('details').open = false));
  $('#day-date').addEventListener('change', e => action(null, () => changeDate(e.target.value)));
  $('#previous-date').addEventListener('click', e => action(e.currentTarget, () => changeDate(shiftDate(state.date, -1))));
  $('#next-date').addEventListener('click', e => action(e.currentTarget, () => changeDate(shiftDate(state.date, 1))));
  $$('button[data-shift]').forEach(b => b.addEventListener('click', () => { state.shift = b.dataset.shift; $$('button[data-shift]').forEach(x => x.classList.toggle('active', x === b)); renderGrid(); }));
  $('#class-search').addEventListener('input', renderGrid);
  $('#class-group').addEventListener('change', renderGrid);
  $('#grid-wrap').addEventListener('click', event => {
    const button = event.target.closest('[data-class]');
    if (!button || button.disabled) return;
    const cid = Number(button.dataset.class), slot = Number(button.dataset.slot);
    openLessonPicker(cid, slot);
  });
  $('#lesson-picker-list').addEventListener('click', event => {
    const button = event.target.closest('[data-picker-teacher]');
    if (button) selectPickerSubject(Number(button.dataset.pickerTeacher), Number(button.dataset.pickerSubject));
  });
  $('#lesson-picker-group').addEventListener('change', event => { if (lessonPicker) { lessonPicker.groupIndex = Number(event.target.value); fillPickerGroups(); refreshLessonPicker(); } });
  $('#lesson-picker-refresh').addEventListener('click', event => action(event.currentTarget, async () => {
    if (!lessonPicker || lessonPicker.saving) return;
    await saveDay(); await loadDay(state.date); fillPickerGroups(); await refreshLessonPicker();
  }));
  $('#lesson-picker-form').addEventListener('submit', event => { event.preventDefault(); savePickerLesson(); });
  $('#lesson-picker-dialog').addEventListener('cancel', event => { if (lessonPicker?.saving) event.preventDefault(); });
  $('#lesson-picker-dialog').addEventListener('close', () => { pickerRequest++; lessonPicker = null; highlightCurrentCell(); });
  $('#lesson-picker-details').addEventListener('click', () => {
    if (!lessonPicker || lessonPicker.saving) return;
    const {cid, slot} = lessonPicker;
    $('#lesson-picker-dialog').close();
    openLesson(cid, slot);
  });
  $$('[data-close]').forEach(b => b.addEventListener('click', () => $('#' + b.dataset.close).close()));
  $('#group-count').addEventListener('change', e => renderGroups(Number(e.target.value), collectGroups()));
  $('#lesson-slot').addEventListener('change', highlightCurrentCell);
  $('#lesson-dialog').addEventListener('close', highlightCurrentCell);
  $('#group-fields').addEventListener('change', e => { if (e.target.matches('[data-field="teacher"]')) updateGroupTeacher(e.target.closest('.group-box')); });
  $('#builder-date').addEventListener('change', e => action(null, () => changeDate(e.target.value)));
  $('#builder-class').addEventListener('change', () => { fillBuilderSlots(); action(null, refreshBuilder); });
  $('#builder-slot').addEventListener('change', () => action(null, refreshBuilder));
  $('#builder-refresh').addEventListener('click', e => action(e.currentTarget, refreshBuilder));
  $('#builder-show-unavailable').addEventListener('change', renderBuilder);
  $$('[data-builder-tab]').forEach(button => {
    button.addEventListener('click', () => { state.builderTab = button.dataset.builderTab; renderBuilderTabs(); });
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      state.builderTab = event.key === 'Home' ? 'available' : event.key === 'End' ? 'busy' : state.builderTab === 'busy' ? 'available' : 'busy';
      renderBuilderTabs();
      $(`[data-builder-tab="${state.builderTab}"]`).focus();
    });
  });
  $('#builder-teachers').addEventListener('click', e => { const button = e.target.closest('[data-builder-select]'); if (button && !button.disabled) selectBuilderTeacher(Number(button.dataset.builderSelect)); });
  $('#builder-class-day').addEventListener('click', e => { const button = e.target.closest('[data-builder-slot]'); if (!button) return; const slot = Number(button.dataset.builderSlot); const cid = state.builder.class_info.id; if (state.day.lessons.some(l => l.class_id === cid && l.slot === slot)) openLesson(cid, slot); else { $('#builder-slot').value = slot; action(null, refreshBuilder); } });
  $('#builder-form').addEventListener('submit', async e => { e.preventDefault(); const button = $('#builder-add'); button.disabled = true; $('#builder-error').textContent = ''; try { await assignBuilderLesson(); } catch (error) { $('#builder-error').textContent = error.message + (error.issues ? ' ' + error.issues.map(i => i.message).join(' ') : ''); await refreshBuilder().catch(() => {}); } finally { button.disabled = !state.builder || !state.builderTeacher || state.builder.occupied; } });
  $('#teacher-class-fields').addEventListener('change', e => { if (e.target.matches('[data-teacher-class]')) $(`[data-class-hours="${e.target.dataset.teacherClass}"]`).disabled = !e.target.checked; });
  $('#teacher-class-search').addEventListener('input', e => { const query = normalizedSearch(e.target.value); $$('[data-class-choice]').forEach(row => row.hidden = !normalizedSearch($('label', row).textContent).includes(query)); });
  for (const [id, checked] of [['teacher-all-classes', true], ['teacher-clear-classes', false]]) $('#' + id).addEventListener('click', () => { $$('[data-teacher-class]').forEach(input => { input.checked = checked; $(`[data-class-hours="${input.dataset.teacherClass}"]`).disabled = !checked; }); });
  $('#teacher-availability-enabled').addEventListener('change', toggleAvailability);
  $('#add-teacher').addEventListener('click', () => openTeacherEditor());
  $('#teacher-search').addEventListener('input', renderTeachers);
  $('#teacher-filter').addEventListener('change', renderTeachers);
  $('#teacher-form').addEventListener('submit', async e => { e.preventDefault(); const button = $('button[type="submit"]', e.target); button.disabled = true; try { await storeTeacher(); } catch (error) { $('#teacher-error').textContent = error.message; } finally { button.disabled = false; } });
  $('#teacher-list').addEventListener('click', e => {
    const edit = e.target.closest('[data-teacher-edit]'), use = e.target.closest('[data-teacher-use]'), archive = e.target.closest('[data-teacher-archive]');
    if (edit) openTeacherEditor(Number(edit.dataset.teacherEdit));
    if (use) action(use, async () => {
      const teacher = state.teachers.find(t => t.id === Number(use.dataset.teacherUse));
      if (!teacher.class_ids?.length) { toast('Сначала отметьте классы, в которых работает учитель.', true); openTeacherEditor(teacher.id); return; }
      state.builderTeacher = null;
      $('#builder-class').value = teacher.class_ids[0];
      fillBuilderSlots();
      openTab('constructor', false);
      await refreshBuilder();
      const choice = state.builder.candidates.find(c => c.teacher.id === teacher.id);
      if (choice?.available && !state.builder.occupied) selectBuilderTeacher(teacher.id);
      else { $('#builder-show-unavailable').checked = true; renderBuilder(); }
      window.scrollTo({top: 0, behavior: 'smooth'});
    });
    if (archive) action(archive, async () => { const teacher = state.teachers.find(t => t.id === Number(archive.dataset.teacherArchive)); await saveDay(); await api(`/api/teachers/${teacher.id}`, 'PUT', {...teacher, active: !teacher.active}); await refreshTeacherCatalog(); toast(teacher.active ? 'Учитель в архиве. Сохранённые уроки сохранены.' : 'Учитель восстановлен'); });
  });
  $('#stop-constructor').addEventListener('click', () => { state.constructorTeacher = null; renderTeachers(); });
  $('#lesson-form').addEventListener('submit', async e => { e.preventDefault(); const button = $('button[type="submit"]', e.target); button.disabled = true; try { await storeLesson(); } catch (error) { $('#lesson-error').textContent = error.message; } finally { button.disabled = false; } });
  $('#delete-lesson').addEventListener('click', e => action(e.currentTarget, () => storeLesson(true)));
  for (const s of [1, 2]) $('#notes-' + s).addEventListener('input', e => { state.day.notes[String(s)] = e.target.value; setDirty(); renderExtrasCount(); });
  $('#add-print-note').addEventListener('click', () => {
    state.day.print_notes ||= [];
    const cls = state.classes.find(c => !state.day.print_notes.some(note => note.class_id === c.id));
    if (!cls) { toast('Для каждого класса уже добавлено объявление.', true); return; }
    state.day.print_notes.push({class_id: cls.id, text: ''}); setDirty(); renderPrintNotes();
  });
  $('#print-notes-list').addEventListener('input', e => {
    const row = e.target.closest('[data-print-note]'); if (!row) return;
    const note = state.day.print_notes[Number(row.dataset.printNote)];
    note.class_id = Number($('[data-print-note-class]', row).value);
    note.text = $('[data-print-note-text]', row).value; setDirty(); renderExtrasCount();
  });
  $('#print-notes-list').addEventListener('click', e => {
    const button = e.target.closest('[data-remove-print-note]'); if (!button) return;
    state.day.print_notes.splice(Number(button.dataset.removePrintNote), 1); setDirty(); renderPrintNotes();
  });
  $('#copy-day').addEventListener('click', e => action(e.currentTarget, openSchedulePicker));
  for (const id of ['copy-source', 'copy-saved-date', 'copy-weekday', 'copy-source-date']) $('#' + id).addEventListener('change', updateSchedulePicker);
  $('#copy-form').addEventListener('submit', async e => { e.preventDefault(); const b = $('#apply-schedule-template'); b.disabled = true; $('#copy-error').textContent = ''; try { await applyScheduleTemplate(); } catch (error) { $('#copy-error').textContent = error.message; } finally { updateSchedulePicker(); } });
  $('#save-weekly').addEventListener('click', e => action(e.currentTarget, async () => { if (!confirm('Заменить недельный шаблон этого дня недели текущим расписанием? Остальные дни недели сохранятся.')) return; await saveDay(true); await api(`/api/day/${state.date}/weekly`, 'POST', {revision:state.revision}); toast('Недельный шаблон этого дня недели сохранён'); }));
  $('#approve-day').addEventListener('click', e => action(e.currentTarget, async () => { await saveDay(); try { await api(`/api/day/${state.date}/approve`, 'POST', {revision:state.revision}); await loadDay(state.date); toast('Расписание дня утверждено'); } catch (error) { if (error.issues) { state.issues = error.issues; renderIssues(); } throw error; } }));
  $('#class-list').addEventListener('click', e => { const b = e.target.closest('[data-move]'); if (!b) return; collectClassDraft(); const i = Number(b.closest('.class-row').dataset.index), target = i + Number(b.dataset.move); if (target < 0 || target >= classDraft.length) return; [classDraft[i], classDraft[target]] = [classDraft[target], classDraft[i]]; renderClasses(); });
  $('#save-classes').addEventListener('click', e => action(e.currentTarget, async () => { await storeClasses(); toast(`Классы сохранены: ${state.classes.length}`); }));
  $('#add-class').addEventListener('click', () => { $('#class-form').reset(); $('#class-error').textContent = ''; $('#class-dialog').showModal(); });
  $('#class-form').addEventListener('submit', async e => {
    e.preventDefault(); const button = $('button[type="submit"]', e.target); button.disabled = true; $('#class-error').textContent = '';
    try { await storeClasses({name: $('#new-class-name').value.trim(), shift: Number($('#new-class-shift').value), print_group: $('#new-class-print-group').value.trim()}); $('#class-dialog').close(); toast('Класс добавлен. Отметьте его в карточках работающих с ним учителей.'); }
    catch (error) { $('#class-error').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('#template-preset').addEventListener('change', e => fillTemplate(state.templates[Number(e.target.value)]));
  $('#apply-reference-layout').addEventListener('click', () => {
    const form = $('#template-form');
    const keep = Object.fromEntries(['school_name', 'director', 'approval_text', 'between_notes'].map(key => [key, form.elements.namedItem(key).value]));
    fillTemplate({...referenceTemplate, ...keep});
    toast('Оформление по образцу выбрано. Нажмите «Сохранить шаблон и звонки».');
  });
  $('#template-form').addEventListener('submit', e => { e.preventDefault(); action($('button[type="submit"]', e.target), async () => { await saveDay(); const template = {}; for (const key of Object.keys(state.settings.template)) { const input = e.target.elements.namedItem(key); template[key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value; } const bells = {}; for (const s of [1, 2]) bells[String(s)] = $('#bells-' + s).value.split('\n').map(x => x.trim()).filter(Boolean); await api('/api/settings', 'PUT', {template, bells}); await refreshState(); await loadDay(state.date); toast('Шаблон и расписание звонков сохранены'); }); });
  $('#generate-preview').addEventListener('click', e => action(e.currentTarget, () => preview()));
  $$('[data-download]').forEach(b => b.addEventListener('click', e => action(e.currentTarget, () => download($('#preview-scope').value))));
  $('#preview-zoom').addEventListener('change', () => action(null, updatePDF));
  $('#preview-page').addEventListener('change', () => action(null, updatePDF));
  $('#preview-previous').addEventListener('click', e => action(e.currentTarget, async () => { $('#preview-page').value = Math.max(1, Number($('#preview-page').value) - 1); await updatePDF(); }));
  $('#preview-next').addEventListener('click', e => action(e.currentTarget, async () => { $('#preview-page').value = Number($('#preview-page').value) + 1; await updatePDF(); }));
  for (const id of ['preview-scope', 'draft-export', 'export-class']) $('#' + id).addEventListener('change', invalidatePreview);
  for (const id of ['preview-scope', 'export-class', 'export-format']) $('#' + id).addEventListener('change', updateExportOptions);
  $('#print-preview').addEventListener('click', e => action(e.currentTarget, async () => { if (!state.pdf) await preview(); window.open(state.pdf, '_blank', 'noopener'); }));
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
}

async function initialize() {
  const data = await api('/api/state');
  const controls = ['#day-bells-hint', '#workspace-metrics', '#logout-account', '#course-form', '#rules-form', '#preview-autofill', '#import-text', '#apply-reference-layout', '#new-class-print-group', '#print-notes-list', '#add-print-note', '#show-hints', '#usage-hints-steps', '#class-count', '#class-nav-count', '#classes-total', '#add-class', '#class-form', '#class-dialog', '#copy-saved-date', '#copy-weekday', '#copy-summary', '#apply-schedule-template', '#builder-busy-teachers', '#builder-available-tab', '#builder-busy-tab', '#builder-date', '#builder-class', '#builder-slot', '#builder-refresh', '#builder-show-unavailable', '#builder-teachers', '#builder-class-day', '#builder-form', '#teacher-class-fields', '#teacher-class-search', '#teacher-all-classes', '#teacher-clear-classes', '#teacher-availability-enabled', '#save-day', '#day-date', '#previous-date', '#next-date', '#class-search', '#grid-wrap',
    '#group-count', '#group-fields', '#add-teacher', '#teacher-search', '#teacher-filter', '#teacher-form',
    '#teacher-list', '#stop-constructor', '#lesson-form', '#delete-lesson', '#notes-1', '#notes-2',
    '#copy-day', '#copy-source', '#copy-form', '#save-weekly', '#approve-day', '#class-list', '#save-classes',
    '#template-preset', '#template-form', '#generate-preview', '#preview-zoom', '#preview-page',
    '#preview-previous', '#preview-next', '#preview-scope', '#draft-export', '#export-class', '#print-preview'];
  controls.push('#usage-hints', '#show-validation', '#schedule-validation', '#schedule-extras-count', '#download-scope', '[data-download="selected"]', '#class-group', '#visible-classes', '#validation-summary-count', '#issue-focus', '#issue-focus-targets', '#clear-issue-focus', '#lesson-picker-dialog', '#lesson-picker-form', '#lesson-picker-list', '#lesson-picker-save');
  const missing = controls.filter(selector => !$(selector));
  if (!Array.isArray(data.teachers) || !data.capabilities?.teacher_schedule || !data.capabilities?.busy_teacher_tab || !data.capabilities?.class_creation || !data.capabilities?.saved_schedule_picker || !data.capabilities?.reference_print_layout || !data.capabilities?.planning_workspace || !data.capabilities?.lesson_picker || missing.length) {
    const error = new Error('Работает предыдущая версия приложения. В окне сервера нажмите Ctrl+C, снова запустите start.bat и обновите эту страницу сочетанием Ctrl+F5.');
    error.needsRestart = true;
    error.missingControls = missing;
    throw error;
  }
  state.classes = data.classes; state.settings = data.settings; state.templates = data.templates; state.teachers = data.teachers || [];
  state.account = data.account || null;
  referenceTemplate = data.reference_template;
  try { $('#show-hints').checked = localStorage.getItem('timetable.showHints') !== 'false'; } catch (_) { /* Hints are enabled by default. */ }
  const requestedDate = new URLSearchParams(location.search).get('date');
  renderSettings(); bindEvents(); await loadDay(/^\d{4}-\d{2}-\d{2}$/.test(requestedDate || '') ? requestedDate : data.today);
  workspace = initializeWorkspace({state, api, action, toast, saveDay, refreshState, loadDay, changeDate, openTab, escapeHTML, formatDate});
  openTab(location.hash.slice(1) || 'schedule');
}

function showStartupError(error) {
  console.error('Application startup failed:', error);
  const status = $('#save-status');
  if (status) { status.textContent = error.needsRestart ? 'Требуется перезапуск приложения' : 'Не удалось запустить приложение'; status.dataset.kind = 'error'; }
  $$('button,input,select,textarea').forEach(control => control.disabled = true);
  const panel = document.createElement('section');
  panel.className = 'card startup-error';
  panel.setAttribute('role', 'alert');
  const title = document.createElement('h2');
  title.textContent = error.needsRestart ? 'Обновление приложения: нужен перезапуск' : 'Не удалось запустить приложение';
  const message = document.createElement('p');
  message.textContent = error.message;
  const reload = document.createElement('button');
  reload.className = 'button primary';
  reload.textContent = 'Обновить страницу после перезапуска';
  reload.addEventListener('click', () => location.reload());
  panel.append(title, message, reload);
  ($('main') || document.body).prepend(panel);
}
initialize().catch(showStartupError);
