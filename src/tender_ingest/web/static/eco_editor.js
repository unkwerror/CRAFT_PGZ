/* Редактор экономики тендера: собирает состояние таблицы, шлёт на сервер
   (/preview — живой пересчёт без сохранения, /save — новая версия) и обновляет
   итоги/сетку/мин.цену/предупреждения. Вся математика на сервере (engine.py) —
   здесь только DOM и debounce. */
function ecoEditorInit() {
  var root = document.getElementById('eco-root');
  if (!root || !root.dataset.calcId) return;
  var reestr = root.dataset.reestr;
  var base = '/tender/' + encodeURIComponent(reestr) + '/economics/' + root.dataset.calcId;

  var fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
  function money(v) { return v == null ? '' : fmt.format(v); }
  function parseNum(s) {
    var t = String(s == null ? '' : s).replace(/[\s ]/g, '').replace(',', '.');
    if (!t) return null;
    var n = Number(t);
    return isFinite(n) ? n : null;
  }

  // Название раздела — авторастущий textarea: текст из ТЗ виден целиком.
  // У скрытой вкладки scrollHeight = 0, поэтому размер считаем только когда
  // элемент видим, и пересчитываем при открытии вкладки «Экономика».
  function autosize(el) {
    if (!el.offsetParent) return;
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
  }
  function autosizeAll() {
    root.querySelectorAll('textarea[data-f=name]').forEach(autosize);
  }
  autosizeAll();
  // Вкладку открывает скрипт табов (снятием hidden) — наблюдаем атрибут напрямую:
  // это покрывает и клик, и прямой заход по #economics, без гонок с порядком скриптов
  var panel = root.closest('[data-panel]');
  if (panel) {
    new MutationObserver(autosizeAll).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
  }
  window.addEventListener('load', autosizeAll);

  function collectBucket(tbodyId) {
    var refs = [];
    var rows = [];
    root.querySelectorAll('#' + tbodyId + ' tr.eco-row').forEach(function (tr) {
      var name = tr.querySelector('[data-f=name]').value.replace(/\s+/g, ' ').trim();
      if (!name) return; // пустое имя — строка не участвует (и не обновляется ответом)
      refs.push(tr);
      rows.push({
        idx: tr.dataset.idx === '' || tr.dataset.idx == null ? null : Number(tr.dataset.idx),
        name: name,
        canon: tr.querySelector('[data-f=canon]').value || null,
        share_pct: parseNum(tr.querySelector('[data-f=share_pct]').value),
        amount: parseNum(tr.querySelector('[data-f=amount]').value),
        touched: tr.dataset.touched || null,
      });
    });
    return { refs: refs, rows: rows };
  }

  function collect() {
    var lines = collectBucket('eco-lines');
    var overheads = collectBucket('eco-overheads');
    var state = {
      lines: lines.rows,
      overheads: overheads.rows,
      base: {
        nmck: parseNum(root.querySelector('[data-b=nmck]').value),
        object_kind: root.querySelector('[data-b=object_kind]').value,
        design_stage: root.querySelector('[data-b=design_stage]').value,
      },
      params: {
        min_margin_pct: parseNum(root.querySelector('[data-p=min_margin_pct]').value),
        target_margin_pct: parseNum(root.querySelector('[data-p=target_margin_pct]').value),
      },
    };
    return { state: state, refs: { lines: lines.refs, overheads: overheads.refs } };
  }

  function setInput(tr, field, value) {
    var el = tr.querySelector('[data-f=' + field + ']');
    if (el && el !== document.activeElement) el.value = value == null ? '' : value;
  }

  function renderRows(refs, linesData) {
    refs.forEach(function (tr, i) {
      var line = linesData[i];
      if (!line) return;
      setInput(tr, 'share_pct', line.share_pct);
      setInput(tr, 'amount', line.amount);
      if (line.source === 'user') {
        var basis = tr.querySelector('[data-role=basis]');
        if (basis && !/правка человека/.test(basis.textContent)) basis.textContent = 'правка человека';
      }
    });
  }

  function renderTotals(pl) {
    var offer = pl.totals.mode === 'offer';
    var nmck = pl.base.nmck;
    var el = function (id) { return document.getElementById(id); };
    el('eco-total-cost').textContent = money(pl.totals.cost);
    el('eco-total-share').textContent = nmck ? (Math.round(pl.totals.cost / nmck * 1000) / 10) : '';
    el('eco-total-note').textContent = offer
      ? 'прибыль при предложенной цене: ' + money(pl.totals.profit_at_offer) + ' ₽ (' + pl.totals.margin_pct + '%)'
      : 'прибыль при НМЦК: ' + money(pl.totals.profit_at_nmck) + ' ₽ (' + pl.totals.margin_pct + '%)';
    var range = el('eco-range');
    if (range) {
      range.textContent = pl.totals_range
        ? 'разброс по аналогам: ' + money(pl.totals_range.p25) + ' – ' + money(pl.totals_range.p75) + ' ₽ (' + pl.totals_range.n + ' аналог.)'
        : '';
    }
    el('eco-offer-row').hidden = !offer;
    el('eco-target-wrap').hidden = !offer;
    if (offer) {
      el('eco-offer-price').textContent = money(pl.totals.price);
      el('eco-offer-margin').textContent = pl.totals.margin_pct;
    }

    el('eco-scenarios-title').textContent = offer ? 'Варианты цены по марже' : 'Сетка понижения цены';
    var th = 'class="py-1 font-medium"';
    el('eco-scenarios-head').innerHTML = '<tr class="text-xs text-slate-400 text-left border-b border-slate-200">'
      + '<th ' + th + '>' + (offer ? 'маржа' : 'снижение') + '</th>'
      + '<th class="py-1 font-medium text-right">цена</th><th class="py-1 font-medium text-right">прибыль</th>'
      + (offer ? '' : '<th class="py-1 font-medium text-right">маржа</th>') + '</tr>';
    el('eco-scenarios').innerHTML = pl.scenarios.map(function (sc) {
      var red = sc.profit < 0 ? ' text-red-700' : '';
      return '<tr class="border-b border-slate-100' + red + '">'
        + '<td class="py-1">' + (offer ? sc.margin_pct + '%' : '−' + sc.reduction_pct + '%') + '</td>'
        + '<td class="py-1 text-right whitespace-nowrap">' + money(sc.price) + '</td>'
        + '<td class="py-1 text-right whitespace-nowrap">' + money(sc.profit) + '</td>'
        + (offer ? '' : '<td class="py-1 text-right">' + sc.margin_pct + '%</td>') + '</tr>';
    }).join('');

    el('eco-minprice').innerHTML = 'Минимально допустимая цена (маржа ' + Math.round(pl.min_price.min_margin_pct) + '%): '
      + '<span class="font-semibold">' + money(pl.min_price.price) + ' ₽</span>'
      + (pl.min_price.max_reduction_pct != null
        ? ' — снижение до <span class="font-semibold">' + pl.min_price.max_reduction_pct + '%</span> от НМЦК'
        : ' — ниже опускаться нельзя');

    el('eco-warnings').innerHTML = (pl.warnings || []).map(function (w) {
      var cls = w.indexOf('⚠') === 0
        ? 'text-red-800 bg-red-50 border-red-200'
        : 'text-amber-800 bg-amber-50 border-amber-200';
      var div = document.createElement('div');
      div.className = 'mb-2 text-[13px] leading-6 border rounded-md px-3 py-1.5 ' + cls;
      div.textContent = w;
      return div.outerHTML;
    }).join('');
  }

  var seq = 0;
  var timer = null;
  var statusEl = document.getElementById('eco-status');
  function preview() {
    var c = collect();
    var my = ++seq;
    statusEl.textContent = 'пересчитываю…';
    fetch(base + '/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(c.state),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (my !== seq || !data.payload) return;
      renderRows(c.refs.lines, data.payload.lines);
      renderRows(c.refs.overheads, data.payload.overheads);
      renderTotals(data.payload);
      statusEl.textContent = 'пересчитано (не сохранено)';
    }).catch(function () { statusEl.textContent = 'сеть недоступна — пересчёт не удался'; });
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(preview, 500);
  }

  root.addEventListener('input', function (e) {
    var f = e.target.dataset.f;
    if (f === 'name') autosize(e.target);
    if (f === 'amount' || f === 'share_pct') e.target.closest('tr').dataset.touched = f;
    if (f || e.target.dataset.b != null || e.target.dataset.p != null) schedule();
  });
  root.addEventListener('change', function (e) {
    if (e.target.dataset.f === 'canon' || e.target.dataset.b || e.target.dataset.p) schedule();
  });
  root.addEventListener('click', function (e) {
    var del = e.target.closest('[data-role=del]');
    if (del) {
      del.closest('tr').remove();
      schedule();
    }
  });

  function addRow(tbodyId) {
    var tpl = document.getElementById('eco-row-tpl');
    var tr = tpl.content.querySelector('tr').cloneNode(true);
    tr.dataset.idx = '';
    document.getElementById(tbodyId).appendChild(tr);
    tr.querySelector('[data-f=name]').focus();
  }
  document.getElementById('eco-add-line').addEventListener('click', function () { addRow('eco-lines'); });
  document.getElementById('eco-add-overhead').addEventListener('click', function () { addRow('eco-overheads'); });

  document.getElementById('eco-save').addEventListener('click', function () {
    var btn = this;
    btn.disabled = true;
    statusEl.textContent = 'сохраняю…';
    fetch(base + '/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collect().state),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.redirect) { location.href = data.redirect; return; }
      btn.disabled = false;
      statusEl.textContent = data.error || 'не удалось сохранить';
    }).catch(function () {
      btn.disabled = false;
      statusEl.textContent = 'сеть недоступна — не сохранено';
    });
  });
}
