(() => {
  'use strict';
  const state = { map: null, profile: null, instrument: '', layers: {}, selected: null };
  const $ = (id) => document.getElementById(id);
  const api = async (url, options = {}) => {
    const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail || data;
      const message = typeof detail === 'string' ? detail : (detail.message || detail.code || `HTTP ${response.status}`);
      throw new Error(message);
    }
    return data;
  };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const formatTime = (value) => value ? new Date(value).toLocaleString('ru-RU', { dateStyle:'short', timeStyle:'short' }) : '—';

  function initMap() {
    if (!window.ol) {
      $('mapError').classList.remove('hidden');
      $('mapError').textContent = 'OpenLayers не загрузился. Проверьте доступ к CDN или установите локальную копию библиотеки.';
      return;
    }
    const base = new ol.layer.Tile({ source: new ol.source.OSM({ crossOrigin: 'anonymous' }) });
    const granules = new ol.layer.Vector({
      source: new ol.source.Vector(),
      style: feature => new ol.style.Style({
        stroke: new ol.style.Stroke({ color: feature.get('instrument') === 'mtvza_gy' ? '#0f8a71' : '#1768d1', width: 1.6 }),
        fill: new ol.style.Fill({ color: feature.get('instrument') === 'mtvza_gy' ? 'rgba(15,138,113,.12)' : 'rgba(23,104,209,.10)' })
      })
    });
    const tracks = new ol.layer.Vector({ source: new ol.source.Vector() });
    const selected = new ol.layer.Vector({
      source: new ol.source.Vector(),
      style: new ol.style.Style({ image: new ol.style.Circle({ radius: 7, fill: new ol.style.Fill({color:'#17202b'}), stroke: new ol.style.Stroke({color:'#fff',width:3}) }) })
    });
    state.layers = { granules, tracks, selected };
    state.map = new ol.Map({
      target: 'map', layers: [base, granules, tracks, selected],
      view: new ol.View({ center: ol.proj.fromLonLat([40, 55]), zoom: 3.6, minZoom: 2, maxZoom: 12 })
    });
    state.map.on('singleclick', event => {
      const [lon, lat] = ol.proj.toLonLat(event.coordinate);
      choosePoint(lat, lon);
    });
    $('showTracks').addEventListener('change', e => tracks.setVisible(e.target.checked));
    $('showGranules').addEventListener('change', e => granules.setVisible(e.target.checked));
  }

  async function loadSatellites() {
    if (!state.map) return;
    const geojson = await api('/api/v1/satellites');
    const source = state.layers.tracks.getSource(); source.clear();
    const format = new ol.format.GeoJSON();
    const features = format.readFeatures(geojson, { featureProjection:'EPSG:3857' });
    features.forEach(feature => {
      const type = feature.get('feature_type'); const color = feature.get('color') || '#365f91';
      feature.setStyle(type === 'satellite'
        ? new ol.style.Style({ image: new ol.style.Circle({ radius:6, fill:new ol.style.Fill({color}), stroke:new ol.style.Stroke({color:'#fff',width:2}) }), text:new ol.style.Text({text:feature.get('name'),offsetY:-14,font:'600 11px sans-serif',fill:new ol.style.Fill({color:'#1d2834'}),stroke:new ol.style.Stroke({color:'#fff',width:3})}) })
        : new ol.style.Style({ stroke:new ol.style.Stroke({color,width:1.4,lineDash:[5,5]}) }));
    });
    source.addFeatures(features);
  }

  async function loadGranules() {
    if (!state.map) return;
    const query = state.instrument ? `?instrument=${encodeURIComponent(state.instrument)}&limit=80` : '?limit=80';
    const geojson = await api('/api/v1/granules' + query);
    const source = state.layers.granules.getSource(); source.clear();
    const features = new ol.format.GeoJSON().readFeatures(geojson, { featureProjection:'EPSG:3857' });
    source.addFeatures(features);
  }

  function setPointMarker(lat, lon) {
    if (!state.map) return;
    const source = state.layers.selected.getSource(); source.clear();
    source.addFeature(new ol.Feature(new ol.geom.Point(ol.proj.fromLonLat([lon,lat]))));
  }

  async function choosePoint(lat, lon) {
    setPointMarker(lat, lon);
    state.selected = {lat, lon};
    $('pointTitle').textContent = `${lat.toFixed(3)}°, ${lon.toFixed(3)}°`;
    $('selectionHelp').textContent = 'Ищется ближайшее поле зрения подходящей гранулы.';
    $('profileLoading').classList.remove('hidden'); $('profileError').classList.add('hidden'); $('profileCard').classList.add('hidden');
    try {
      const payload = { latitude:lat, longitude:lon, instrument:state.instrument || null };
      state.profile = await api('/api/v1/profile/retrieve', { method:'POST', body:JSON.stringify(payload) });
      renderProfile(state.profile);
    } catch (error) {
      $('profileError').textContent = error.message;
      $('profileError').classList.remove('hidden');
      $('selectionHelp').textContent = 'Профиль не получен. Проверьте наличие гранул и обученной модели.';
    } finally { $('profileLoading').classList.add('hidden'); }
  }

  function renderProfile(data) {
    const obs = data.observation;
    $('selectionHelp').textContent = 'Использовано ближайшее спутниковое поле зрения.';
    $('distanceBadge').textContent = `${obs.distance_km.toFixed(1)} км`;
    $('distanceBadge').classList.remove('hidden');
    $('selectionMeta').innerHTML = [
      ['Спутник',obs.satellite],['Прибор',obs.instrument],['Время',formatTime(obs.time)],['Гранула',obs.granule_id],
      ['Поле зрения',obs.fov_index],['Зенитный угол',`${obs.satellite_zenith_deg.toFixed(1)}°`]
    ].map(([k,v]) => `<div class="meta-item"><span>${escapeHtml(k)}</span><strong title="${escapeHtml(v)}">${escapeHtml(v)}</strong></div>`).join('');
    $('selectionMeta').classList.remove('hidden');
    drawProfile(data.profile);
    const model = data.model;
    $('modelInfo').innerHTML = `<strong>${escapeHtml(model.instrument)} · ${escapeHtml(model.version)}</strong><br>Статус: ${escapeHtml(model.status)} · RMSE T: ${model.temperature_validation_rmse_k == null ? 'нет оценки' : Number(model.temperature_validation_rmse_k).toFixed(2)+' K'}`;
    $('profileWarnings').innerHTML = (data.warnings || []).map(w => `<div class="notice warning">${escapeHtml(w)}</div>`).join('');
    $('profileCard').classList.remove('hidden');
  }

  function drawProfile(profile) {
    const svg = $('profileChart'); const W=540,H=400,m={l:58,r:48,t:20,b:38};
    const pressure = profile.map(p=>p.pressure_hpa); const temp=profile.map(p=>p.temperature_c); const rh=profile.map(p=>p.relative_humidity_pct);
    const logp = p => Math.log(p); const pMin=Math.min(...pressure),pMax=Math.max(...pressure);
    const tMin=Math.floor((Math.min(...temp)-5)/10)*10, tMax=Math.ceil((Math.max(...temp)+5)/10)*10;
    const y=p=>m.t+(logp(p)-logp(pMin))/(logp(pMax)-logp(pMin))*(H-m.t-m.b);
    const xt=t=>m.l+(t-tMin)/(tMax-tMin)*(W-m.l-m.r); const xr=r=>m.l+r/100*(W-m.l-m.r);
    const levels=[1000,850,700,500,300,200,100,50,20,10].filter(p=>p>=pMin&&p<=pMax);
    const tempPath=temp.map((v,i)=>`${i?'L':'M'}${xt(v).toFixed(1)},${y(pressure[i]).toFixed(1)}`).join(' ');
    const rhPath=rh.map((v,i)=>`${i?'L':'M'}${xr(v).toFixed(1)},${y(pressure[i]).toFixed(1)}`).join(' ');
    const grid=levels.map(p=>`<line x1="${m.l}" y1="${y(p)}" x2="${W-m.r}" y2="${y(p)}" stroke="#e1e7ed"/><text x="${m.l-8}" y="${y(p)+4}" text-anchor="end" font-size="10" fill="#6b7785">${p}</text>`).join('');
    const tTicks=[tMin,(tMin+tMax)/2,tMax].map(t=>`<text x="${xt(t)}" y="${H-12}" text-anchor="middle" font-size="10" fill="#a13f33">${Math.round(t)}°</text>`).join('');
    const rTicks=[0,50,100].map(r=>`<text x="${xr(r)}" y="12" text-anchor="middle" font-size="10" fill="#1768d1">${r}%</text>`).join('');
    svg.innerHTML=`${grid}<line x1="${m.l}" y1="${m.t}" x2="${m.l}" y2="${H-m.b}" stroke="#aab5c0"/><path d="${tempPath}" fill="none" stroke="#d14a3a" stroke-width="2.6" stroke-linejoin="round"/><path d="${rhPath}" fill="none" stroke="#1768d1" stroke-width="2.2" stroke-linejoin="round" opacity=".9"/>${tTicks}${rTicks}<text x="12" y="${H/2}" transform="rotate(-90 12 ${H/2})" text-anchor="middle" font-size="10" fill="#6b7785">Давление, гПа</text>`;
  }

  async function loadStatus() {
    const [health,status] = await Promise.all([api('/api/v1/health'), api('/api/v1/status')]);
    const badge=$('healthBadge'); const satOk=health.satdump.ok;
    badge.textContent = satOk ? 'Система готова' : 'SatDump требует настройки'; badge.className='status-pill '+(satOk?'':'warning');
    const c=status.counts;
    $('counters').innerHTML=[['Зонды',c.soundings],['Гранулы',c.satellite_granules],['Пары',c.matchups],['В очереди',c.jobs_queued]].map(([k,v])=>`<div class="counter"><strong>${v}</strong><span>${k}</span></div>`).join('');
    $('jobsList').innerHTML=(status.jobs||[]).slice(0,8).map(j=>`<div class="job"><span class="job-state ${escapeHtml(j.status)}"></span><div class="job-main"><strong>${escapeHtml(j.job_type)}</strong><span>${escapeHtml(j.message||'')}</span></div><time>${formatTime(j.updated_at)}</time></div>`).join('') || '<div class="muted-text">Заданий пока нет</div>';
  }

  async function enqueueJob(jobType, button) {
    button.disabled=true;
    try { await api('/api/v1/jobs',{method:'POST',body:JSON.stringify({job_type:jobType,dedupe_key:`ui:${jobType}`})}); await loadStatus(); }
    catch(error){ alert(error.message); }
    finally { button.disabled=false; }
  }

  function downloadProfile() {
    if (!state.profile) return;
    const blob=new Blob([JSON.stringify(state.profile,null,2)],{type:'application/json'}); const url=URL.createObjectURL(blob);
    const a=document.createElement('a'); a.href=url; a.download=`satprof-${state.profile.observation.instrument}-${state.profile.observation.time.replace(/[:.]/g,'-')}.json`; a.click(); URL.revokeObjectURL(url);
  }

  async function refreshAll() {
    $('refreshButton').disabled=true;
    try { await Promise.all([loadStatus(),loadSatellites(),loadGranules()]); }
    catch(error){ $('healthBadge').textContent='Ошибка API'; $('healthBadge').className='status-pill error'; console.error(error); }
    finally { $('refreshButton').disabled=false; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initMap();
    $('instrumentFilter').addEventListener('change', e => { state.instrument=e.target.value; loadGranules(); if(state.selected) choosePoint(state.selected.lat,state.selected.lon); });
    $('refreshButton').addEventListener('click',refreshAll); $('downloadProfile').addEventListener('click',downloadProfile);
    document.querySelectorAll('[data-job]').forEach(button=>button.addEventListener('click',()=>enqueueJob(button.dataset.job,button)));
    refreshAll(); setInterval(loadStatus,15000); setInterval(loadSatellites,60000);
  });
})();
