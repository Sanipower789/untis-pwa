/* Personal homework stays server-owned, separate from legacy profile writes. */
window.Homework = (() => {
  const $ = id => document.getElementById(id);
  const form = $('homework-form');
  const course = $('homework-course');
  const text = $('homework-text');
  const mode = $('homework-mode');
  const dateInput = $('homework-date');
  const list = $('homework-list');
  const controls = {
    markersEnabled: $('homework-markers'), markerColor: $('homework-color'),
    markerStyle: $('homework-style'), reminders: $('homework-notifications'),
    reminderDays: $('homework-days'), reminderTime: $('homework-time'),
  };
  let items = [], settings = {}, account = '', version = 0, editing = null, busy = false;
  let ready = false;
  let openedNotification = false;
  const errors = {
    unauthorized: 'Bitte erneut anmelden.', homework_not_found: 'Diese Aufgabe existiert nicht mehr.',
    homework_course_not_selected: 'Bitte einen ausgewählten Kurs deiner Stufe wählen.',
    homework_limit: 'Maximal 100 Aufgaben. Bitte alte Aufgaben löschen.',
    invalid_homework: 'Bitte Fach, Aufgabentext und Fälligkeit prüfen.',
  };
  function status(message, bad = false, id = 'homework-status') {
    $(id).textContent = message;
    $(id).classList.toggle('status-error', bad);
  }
  async function request(url, method = 'GET', payload) {
    const response = await fetch(url, { method, cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(errors[data.error] || 'Nicht gespeichert. Bitte Verbindung prüfen und erneut versuchen.');
    return data;
  }
  const selected = () => getSelectedCourseKeys();
  const active = item => item.course.startsWith(getGrade() + ':') && selected().has(item.course);
  const label = key => COURSE_LABEL_BY_KEY.get(key) || key.split(':').slice(1).join(':');
  const deadline = item => item.due && item.resolution !== 'pending' ? item.due : null;
  function dueLabel(item) {
    const due = deadline(item);
    if (!due) return item.mode === 'next' ? 'Nächste Stunde noch nicht bekannt' : formatDate(item.date);
    const day = formatDate(due.date);
    const result = day + (due.start ? `, ${due.start} Uhr` : '');
    return item.resolution === 'unavailable' ? `${result} · Termin nicht bestätigt` : result;
  }
  function subjects() {
    const current = course.value;
    const keys = [...selected()].filter(key => key.startsWith(getGrade() + ':'));
    if (editing) {
      const old = items.find(item => item.id === editing)?.course;
      if (old && !keys.includes(old)) keys.push(old);
    }
    course.replaceChildren(...keys.map(key => new Option(`${label(key)} (${key.split(':')[0]})`, key)));
    if (keys.includes(current)) course.value = current;
    $('homework-save').disabled = busy || !ready || keys.length === 0;
  }
  function apply(data) {
    items = Array.isArray(data.homework) ? data.homework : [];
    settings = data.settings || {};
    ready = true;
    for (const [key, input] of Object.entries(controls)) {
      if (input.type === 'checkbox') input.checked = !!settings[key];
      else input.value = String(settings[key]);
    }
    controls.reminderDays.disabled = !settings.reminders;
    controls.reminderTime.disabled = !settings.reminders;
    $('homework-push-hint').textContent = settings.reminders &&
      (typeof Notification === 'undefined' || Notification.permission !== 'granted' || !PushNotifications.getPreferences().enabled)
      ? 'In der Kategorie Benachrichtigungen aktivieren.' : '';
    render();
    rebuildGridNow();
  }
  function reset() {
    editing = null;
    form.reset();
    $('homework-date-row').hidden = true;
    dateInput.required = false;
    $('homework-save').textContent = 'Hinzufügen';
    $('homework-reset').hidden = true;
    subjects();
  }
  function render() {
    subjects();
    $('homework-remind').disabled = busy || !ready || !settings.reminders;
    for (const [key, input] of Object.entries(controls)) {
      input.disabled = busy || !ready || ((key === 'reminderDays' || key === 'reminderTime') && !settings.reminders);
    }
    const showDone = $('homework-show-done').checked;
    const visible = items.filter(item => item.done === showDone).sort((a, b) =>
      (deadline(a)?.date || '9999').localeCompare(deadline(b)?.date || '9999'));
    list.replaceChildren();
    if (!visible.length) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = ready ? (showDone ? 'Keine erledigten Aufgaben.' : 'Keine offenen Aufgaben.') : 'Aufgaben werden geladen.';
      list.appendChild(empty);
    }
    visible.forEach(item => {
      const row = document.createElement('article');
      row.className = 'homework-item';
      row.dataset.homeworkId = item.id;
      const body = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = item.text;
      const details = document.createElement('small');
      details.textContent = `${label(item.course)} (${item.course.split(':')[0]}) · ${dueLabel(item)}${active(item) ? '' : ' · Nicht in Kursauswahl'}`;
      const due = deadline(item);
      if (!item.done && due && new Date(`${due.date}T${due.start || '23:59'}`) < new Date()) {
        details.textContent += ' · Überfällig';
      }
      body.append(title, details);
      const actions = document.createElement('div'); actions.className = 'homework-actions';
      for (const [name, handler] of [
        [item.done ? 'Wieder öffnen' : 'Erledigen', () => mutate(`/api/homework/${item.id}`, 'PATCH', { done: !item.done })],
        ['Bearbeiten', () => {
          editing = item.id; subjects(); course.value = item.course;
          text.value = item.text; mode.value = item.mode; dateInput.value = item.date;
          $('homework-remind').checked = item.remind;
          updateMode(); $('homework-save').textContent = 'Speichern'; $('homework-reset').hidden = false;
          text.focus();
        }],
        ['Löschen', () => { if (confirm('Diese Hausaufgabe löschen?')) mutate(`/api/homework/${item.id}`, 'DELETE'); }],
      ]) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'plain'; button.textContent = name; button.disabled = busy;
        button.addEventListener('click', handler); actions.appendChild(button);
      }
      body.appendChild(actions); row.append(body); list.appendChild(row);
    });
  }
  async function refresh() {
    if (!Auth.isLoggedIn() || busy) return;
    const user = Auth.username();
    if (account !== user) authChanged(user);
    const token = ++version;
    try {
      const data = await request('/api/homework');
      if (token !== version || account !== user || !Auth.isLoggedIn()) return;
      apply(data); status('');
      if (!openedNotification && new URLSearchParams(window.location.search).get('homework') === '1') {
        openedNotification = true; open();
      }
    } catch (error) {
      if (token !== version || account !== user) return;
      status('Hausaufgaben konnten nicht aktualisiert werden. ' + (ready ? 'Zuletzt geladener Stand.' : 'Bitte erneut versuchen.'), true);
    }
  }
  async function mutate(url, method, payload, resetForm = false) {
    if (busy || !Auth.isLoggedIn()) return;
    busy = true;
    const token = ++version, user = account;
    let saved = false;
    render(); status('Wird gespeichert.');
    try {
      const data = await request(url, method, payload);
      if (version !== token || account !== user || !Auth.isLoggedIn()) return;
      apply(data);
      saved = true;
      if (resetForm) reset();
      status('Gespeichert.');
    } catch (error) {
      if (version === token && account === user) status(error.message || 'Speichern fehlgeschlagen.', true);
    } finally {
      if (account === user) { busy = false; render(); }
    }
    if (saved && account === user) await refresh();
  }
  function updateMode() {
    $('homework-date-row').hidden = mode.value !== 'date';
    dateInput.required = mode.value === 'date';
  }
  function open(ids = []) {
    activateSidebarPanel($('panelHomework'), $('navHomework'));
    sidebarShow(); render();
    if (ids.length) {
      const row = [...list.children].find(el => ids.includes(el.dataset.homeworkId));
      row?.scrollIntoView({ block: 'nearest' });
    }
  }
  function mark(element, matches) {
    if (!settings.markersEnabled || !matches.length) return;
    if (settings.markerStyle === 'outline') {
      element.classList.add('homework-outline');
      element.style.setProperty('--homework-color', settings.markerColor || '#14b8a6');
    }
    const button = document.createElement('button'); button.type = 'button';
    button.className = `homework-marker homework-marker-${settings.markerStyle || 'badge'}`;
    const caption = `${matches.length} Hausaufgabe${matches.length === 1 ? '' : 'n'}: ${matches.map(item => item.text).join('; ')}`;
    button.title = caption; button.setAttribute('aria-label', caption);
    button.style.setProperty('--homework-color', settings.markerColor || '#14b8a6');
    button.textContent = settings.markerStyle === 'dot' ? '' : 'HA';
    button.addEventListener('click', event => { event.stopPropagation(); open(matches.map(item => item.id)); });
    element.appendChild(button);
  }
  function forLesson(lesson) {
    if (!lesson || lesson.status === 'entfaellt') return [];
    const keys = new Set([lesson.subject_original, lesson.subject].filter(Boolean).map(subject => resolveCourseKey(subject, lesson.grade)));
    return items.filter(item => !item.done && active(item) && keys.has(item.course) &&
      (item.mode === 'date' ? item.date === lesson.date : item.resolution === 'resolved' &&
        item.due?.date === lesson.date && item.due?.start === lesson.start));
  }
  function renderLessonDetails(lesson) {
    const region = $('lesson-overlay-homework');
    if (!region) return;
    region.replaceChildren();
    const matches = forLesson(lesson);
    region.hidden = !matches.length;
    if (!matches.length) return;
    const heading = document.createElement('h3'); heading.textContent = 'Hausaufgaben';
    region.appendChild(heading);
    matches.forEach(item => {
      const paragraph = document.createElement('p'); paragraph.textContent = item.text;
      region.appendChild(paragraph);
    });
  }
  function decorateLesson(element, lesson) {
    mark(element, forLesson(lesson).filter(item => item.mode === 'next'));
  }
  function decorateDay(element, day) {
    mark(element, items.filter(item => !item.done && active(item) && item.mode === 'date' && item.date === day));
  }
  function decorateWeek(element, weekStart) {
    if (!settings.markersEnabled || !weekStart) return;
    const start = new Date(`${weekStart}T12:00:00`);
    for (const offset of [5, 6]) {
      const day = new Date(start); day.setDate(day.getDate() + offset);
      const iso = toISODate(day);
      const matches = items.filter(item => !item.done && active(item) && item.mode === 'date' && item.date === iso);
      if (!matches.length) continue;
      const row = document.createElement('div'); row.className = 'homework-weekend';
      row.textContent = `${offset === 5 ? 'Samstag' : 'Sonntag'}, ${formatDate(iso)}`;
      mark(row, matches); element.appendChild(row);
    }
  }
  function authChanged(user) {
    version++; account = user || ''; items = []; settings = {}; ready = false; busy = false;
    reset(); render(); rebuildGridNow();
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    mutate(editing ? `/api/homework/${editing}` : '/api/homework', editing ? 'PATCH' : 'POST', {
      course: course.value, text: text.value.trim(), mode: mode.value,
      date: mode.value === 'date' ? dateInput.value : '', remind: $('homework-remind').checked,
    }, true);
  });
  $('homework-reset').addEventListener('click', reset);
  mode.addEventListener('change', updateMode);
  $('homework-show-done').addEventListener('change', render);
  $('navHomework').addEventListener('click', () => { open(); refresh(); });
  for (const [key, input] of Object.entries(controls)) {
    input.addEventListener('change', () => mutate('/api/homework-settings', 'PUT', {
      [key]: input.type === 'checkbox' ? input.checked : input.value,
    }));
  }
  return { refresh, authChanged, decorateLesson, decorateDay, decorateWeek, renderLessonDetails, open };
})();
