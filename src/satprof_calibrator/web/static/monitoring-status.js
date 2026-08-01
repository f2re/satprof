(() => {
  'use strict';

  const target = () => document.getElementById('monitoringStatus');
  const formatAge = seconds => {
    if (seconds == null || !Number.isFinite(Number(seconds))) return 'нет данных';
    const value = Number(seconds);
    if (value < 120) return `${Math.round(value)} с`;
    if (value < 7200) return `${Math.round(value / 60)} мин`;
    if (value < 172800) return `${(value / 3600).toFixed(1)} ч`;
    return `${(value / 86400).toFixed(1)} сут`;
  };

  function row(label, value, state) {
    const element = document.createElement('div');
    element.className = 'source-row';
    const dot = document.createElement('span');
    dot.className = `source-dot ${state || 'muted'}`;
    const text = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = label;
    const small = document.createElement('span');
    small.textContent = value;
    text.append(strong, small);
    element.append(dot, text);
    return element;
  }

  function stateClass(status) {
    if (status === 'ok') return 'ok';
    if (status === 'error') return 'warning';
    return status === 'warning' ? 'warning' : 'muted';
  }

  async function refreshMonitoring() {
    const container = target();
    if (!container) return;
    try {
      const response = await fetch('/api/v1/monitoring', {headers: {'Accept': 'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const report = await response.json();
      container.replaceChildren();
      container.append(row(
        'Готовность',
        report.ready ? `готово · ${report.state}` : `не готово · ${report.summary.critical_errors} критических`,
        report.ready ? (report.state === 'ok' ? 'ok' : 'warning') : 'warning'
      ));
      const checks = Object.fromEntries((report.checks || []).map(item => [item.name, item]));
      const satdump = checks.satdump;
      if (satdump) container.append(row('SatDump', satdump.message, stateClass(satdump.status)));
      const workspace = checks.workspace;
      if (workspace) container.append(row('Хранилище', workspace.message, stateClass(workspace.status)));
      const jobs = checks.jobs;
      if (jobs) container.append(row('Очередь', jobs.message, stateClass(jobs.status)));
      const satellite = (report.freshness || {}).satellite;
      if (satellite) container.append(row(
        'Спутниковые данные',
        `возраст ${formatAge(satellite.age_seconds)}`,
        satellite.age_seconds == null || satellite.age_seconds > satellite.max_age_seconds ? 'warning' : 'ok'
      ));
    } catch (error) {
      container.replaceChildren(row('Мониторинг', `ошибка: ${error.message}`, 'warning'));
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    refreshMonitoring();
    setInterval(refreshMonitoring, 15000);
  });
})();
