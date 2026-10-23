'use strict';
const $ = selector => document.querySelector(selector);
let destination = '/';
async function submit(form, path) {
  const button = form.querySelector('[type=submit]');
  const error = form.querySelector('.form-error');
  error.textContent = ''; button.disabled = true;
  try {
    const response = await fetch('/api/account/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.fromEntries(new FormData(form)))});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Не удалось выполнить запрос.');
    if (value.recovery_code) {
      $('#recovery-code').textContent = value.recovery_code;
      $('#recovery-result').hidden = false;
      $('#recovery-result').scrollIntoView({behavior: 'smooth', block: 'center'});
      if (path === 'reset') { $('#recovery-dialog').close(); destination = '/account'; }
    } else location.assign('/');
  } catch (reason) { error.textContent = reason.message; }
  finally { button.disabled = false; }
}
for (const [id, path] of [['login', 'login'], ['register', 'register'], ['recovery', 'reset']]) $('#' + id + '-form').addEventListener('submit', event => { event.preventDefault(); submit(event.target, path); });
$('#open-recovery').addEventListener('click', () => $('#recovery-dialog').showModal());
$('#close-recovery').addEventListener('click', () => $('#recovery-dialog').close());
$('#continue-to-app').addEventListener('click', () => location.assign(destination));
fetch('/api/account/state').then(response => response.json()).then(value => {
  $('#registration-note').textContent = value.required ? 'Для новой школы создаётся отдельная база без данных других аккаунтов.' : 'Первый аккаунт получит доступ к уже заполненной локальной школе. Следующие аккаунты создают отдельные школы.';
}).catch(() => {});
