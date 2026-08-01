(() => {
  'use strict';
  const container = () => document.getElementById('sourceStatus');
  const formatTime = value => value ? new Date(value).toLocaleString('ru-RU', {dateStyle:'short', timeStyle:'short'}) : 'нет данных';

  function line(label, value, state) {
    const row = document.createElement('div');
    row.className = 'source-row';
    const dot = document.createElement('span');
    dot.className = `source-dot ${state || 'muted'}`;
    const text = document.createElement('div');
    const strong = document.createElement('strong'); strong.textContent = label;
    const small = document.createElement('span'); small.textContent = value;
    text.append(strong, small); row.append(dot, text);
    return row;
  }

  async function refreshSources() {
    const target = container();
    if (!target) return;
    try {
      const response = await fetch('/api/v1/sources', {headers:{'Accept':'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      target.replaceChildren();
      const wis = data.wis2 || {};
      const wisState = wis.status === 'connected' ? 'ok' : (wis.status === 'disabled' ? 'muted' : 'warning');
      const wisText = wis.status === 'connected'
        ? `подключено · принято ${wis.received || 0}, загружено ${wis.downloaded || 0}`
        : (wis.status === 'disabled' ? 'отключено в конфигурации' : `состояние: ${wis.status || 'не запущено'}`);
      target.append(line('WIS 2.0', wisText, wisState));
      const entries = Object.entries(data.ingested || {}).sort(([a],[b]) => a.localeCompare(b));
      if (!entries.length) target.append(line('Зондирование', 'файлы ещё не обработаны', 'muted'));
      for (const [name, item] of entries) {
        target.append(line(name, `${item.records || 0} профилей · ${formatTime(item.last_processed_at)}`, item.errors ? 'warning' : 'ok'));
      }
    } catch (error) {
      target.replaceChildren(line('Источники', `ошибка диагностики: ${error.message}`, 'warning'));
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    refreshSources();
    setInterval(refreshSources, 15000);
  });
})();
