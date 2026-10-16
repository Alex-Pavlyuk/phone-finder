"""
Генерация автономного HTML-дашборда (без внешних зависимостей и CDN).

Отчёт устроен вокруг модели, а не предложения: карточка — это телефон, внутри
неё — цены всех магазинов, где он нашёлся. Так видно и «что купить», и «где
дешевле», причём одной строкой, а не пятью почти одинаковыми карточками.

Пометки «звёздочка» и «скрыть» живут в localStorage браузера по ключу модели,
поэтому переживают перегенерацию отчёта.

Шаблон — «сырая» строка: внутри есть регулярное выражение с `\\/`, и в обычной
строке Python ругался бы на нераспознанную escape-последовательность.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import SOURCE_LABEL, Phone

TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phone Finder — __TITLE__</title>
<style>
:root {
  --bg:#f6f7f9; --card:#fff; --fg:#16181d; --muted:#6b7280; --line:#e3e6ea;
  --accent:#2563eb; --new:#16a34a; --drop:#dc2626; --warn:#f59e0b;
  --star:#eab308; --chip:#eef1f5;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#171a21; --fg:#e8eaed; --muted:#9aa3af; --line:#252a33;
          --accent:#60a5fa; --new:#4ade80; --drop:#f87171; --warn:#fbbf24;
          --star:#facc15; --chip:#222732; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.45 -apple-system,
       BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
header { position:sticky; top:0; z-index:10; background:var(--bg);
         border-bottom:1px solid var(--line); padding:12px 20px 10px; }
h1 { margin:0 0 2px; font-size:19px; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:13px; }
.controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; }
input[type=search], select {
  background:var(--card); color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:7px 10px; font-size:14px; }
input[type=search] { min-width:200px; flex:1 1 200px; }
.tabs { display:flex; gap:6px; flex-wrap:wrap; }
.tab { padding:7px 13px; border-radius:8px; border:1px solid var(--line);
       background:var(--card); cursor:pointer; font-size:14px; color:var(--fg); }
.tab.on { background:var(--accent); border-color:var(--accent); color:#fff; }
.tab .n { opacity:.65; margin-left:5px; font-variant-numeric:tabular-nums; }
.toggle { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--muted);
          cursor:pointer; user-select:none; white-space:nowrap; }
.range { display:flex; align-items:center; gap:7px; font-size:13px; color:var(--muted); }
.range input { accent-color:var(--accent); }
main { padding:16px 20px 60px; }
.grid { display:grid; gap:14px;
        grid-template-columns:repeat(auto-fill, minmax(330px, 1fr)); }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        overflow:hidden; display:flex; flex-direction:column; position:relative; }
.card.isnew { border-color:var(--new); box-shadow:0 0 0 1px var(--new) inset; }
.card.starred { border-color:var(--star); box-shadow:0 0 0 1px var(--star) inset; }
.card.dimmed { opacity:.55; }
/* max-height, а не только пропорция: картинки магазинов вертикальные, и в
   широкой колонке карточка превращалась бы в один большой снимок телефона. */
.thumb { aspect-ratio:4/3; max-height:210px; background:var(--chip);
         object-fit:contain; width:100%; display:block; padding:8px; }
.acts { position:absolute; top:8px; right:8px; display:flex; gap:6px; }
.acts button { width:30px; height:30px; border-radius:8px; border:none; cursor:pointer;
        background:rgba(12,14,18,.62); color:#fff; font-size:15px; line-height:1;
        backdrop-filter:blur(3px); }
.acts button.on { background:var(--star); color:#1a1a1a; }
.body { padding:11px 13px 13px; display:flex; flex-direction:column; gap:7px; flex:1; }
.row { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.name { font-weight:600; font-size:15px; line-height:1.25; }
.price { font-size:18px; font-weight:700; white-space:nowrap; }
.specs { display:flex; flex-wrap:wrap; gap:5px; }
.chip { background:var(--chip); border-radius:6px; padding:2px 7px; font-size:12px;
        color:var(--muted); white-space:nowrap; }
.chip.hi { background:color-mix(in srgb, var(--new) 22%, transparent); color:var(--fg); }
.chip.warn { background:color-mix(in srgb, var(--warn) 25%, transparent); color:var(--fg); }
.badges { display:flex; gap:6px; flex-wrap:wrap; }
.badge { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
         padding:2px 7px; border-radius:5px; color:#fff; }
.badge.new { background:var(--new); }
.badge.drop { background:var(--drop); }
.badge.warn { background:var(--warn); color:#1a1a1a; }
.stores { display:flex; flex-direction:column; gap:3px; font-size:13px; }
.stores a { color:var(--accent); text-decoration:none; display:flex;
            justify-content:space-between; gap:10px; }
.stores a:hover { text-decoration:underline; }
.stores .st { color:var(--muted); }
.foot { margin-top:auto; display:flex; justify-content:space-between; align-items:center;
        gap:8px; padding-top:6px; }
a.open { color:#fff; background:var(--accent); text-decoration:none; padding:7px 12px;
         border-radius:8px; font-size:13px; font-weight:600; }
.score { display:flex; align-items:center; gap:7px; font-size:12px; color:var(--muted); }
.bar { width:56px; height:5px; border-radius:3px; background:var(--chip); overflow:hidden; }
.bar i { display:block; height:100%; background:var(--accent); }
.empty { color:var(--muted); padding:48px 0; text-align:center; }
details.why { font-size:12px; color:var(--muted); }
details.why summary { cursor:pointer; }
details.why div { padding-top:4px; }
</style>
</head>
<body>
<header>
  <h1>Phone Finder — камера с зумом, батарея, IP68, AI</h1>
  <div class="sub">__SUBTITLE__</div>
  <div class="controls">
    <div class="tabs">
      <button class="tab on" data-tab="match">Подходят<span class="n" id="n-match"></span></button>
      <button class="tab" data-tab="near">Почти<span class="n" id="n-near"></span></button>
      <button class="tab" data-tab="soon">Скоро<span class="n" id="n-soon"></span></button>
      <button class="tab" data-tab="ali">AliExpress<span class="n" id="n-ali"></span></button>
      <button class="tab" data-tab="star">★ Избранное<span class="n" id="n-star"></span></button>
      <button class="tab" data-tab="hidden">Скрытые<span class="n" id="n-hidden"></span></button>
    </div>
  </div>
  <div class="controls">
    <input type="search" id="q" placeholder="Поиск: модель, марка, магазин…">
    <select id="sort">
      <option value="score">по рейтингу</option>
      <option value="price">сначала дешёвые</option>
      <option value="price-desc">сначала дорогие</option>
      <option value="battery">по батарее</option>
      <option value="zoom">по зуму</option>
      <option value="new">сначала новинки</option>
    </select>
    <select id="brand"><option value="">все марки</option></select>
    <label class="range">до <input type="range" id="pmax" min="0" max="__MAXPRICE__"
      step="100" value="__MAXPRICE__"><span id="pmax-v"></span></label>
    <label class="toggle"><input type="checkbox" id="only-new"> только новинки</label>
    <label class="toggle"><input type="checkbox" id="only-sure"> только подтверждённые</label>
  </div>
</header>
<main><div class="grid" id="grid"></div><div class="empty" id="empty" hidden>Ничего не нашлось</div></main>
<script>
const DATA = __DATA__;
const SOON = __SOON__;
const ALI = __ALI__;
const PREFS_KEY = 'phonefinder.prefs';
const MARKS_KEY = 'phonefinder.marks';
const el = id => document.getElementById(id);

// localStorage есть не всегда: страница, открытая как data:-URL или при
// запрещённых сайтах-данных, на первом же обращении бросает исключение — и
// раньше на этом умирал весь скрипт, то есть отчёт оказывался пустым целиком.
// Пометки «звёздочка» и «скрыть» — приятная деталь, а не условие работы,
// поэтому при недоступном хранилище они просто не переживают перезагрузку.
const store = (() => {
  try {
    localStorage.setItem('phonefinder.probe', '1');
    localStorage.removeItem('phonefinder.probe');
    return localStorage;
  } catch (err) {
    return { getItem: () => null, setItem: () => {} };
  }
})();
const read = key => { try { return JSON.parse(store.getItem(key) || '{}'); } catch (e) { return {}; } };
const marks = read(MARKS_KEY);
const prefs = read(PREFS_KEY);
let tab = prefs.tab || 'match';

const saveMarks = () => store.setItem(MARKS_KEY, JSON.stringify(marks));
const savePrefs = () => store.setItem(PREFS_KEY, JSON.stringify(prefs));
const money = v => v == null ? '—' : v.toLocaleString('pl-PL') + ' zł';
const mark = k => marks[k] || {};

function chips(p) {
  const s = p.spec, out = [];
  if (s.battery_mah) out.push([`${s.battery_mah} мАч`, s.battery_mah >= __BATTERY__ ? 'hi' : 'warn']);
  // Точного числа нет, но источник гарантировал нижнюю границу своим фильтром.
  else if (s.battery_min) out.push([`≥${s.battery_min} мАч`, s.battery_min >= __BATTERY__ ? 'hi' : 'warn']);
  if (s.optical_zoom) out.push([`зум ${s.optical_zoom}×`, s.optical_zoom >= __ZOOM__ ? 'hi' : 'warn']);
  else out.push(['зум ?', 'warn']);
  if (s.ois) out.push(['OIS', 'hi']);
  if (s.ip) out.push([s.ip, 'hi']);
  if (s.ai && s.ai.length) out.push([s.ai.join(' · '), 'hi']);
  if (s.main_mp) out.push([`${s.main_mp} Mpix${s.tele_mp ? ' + tele ' + s.tele_mp : ''}`, '']);
  if (s.screen_in) out.push([`${s.screen_in}"${s.refresh_hz ? ' ' + s.refresh_hz + 'Hz' : ''}`, '']);
  if (s.charge_w) out.push([`${s.charge_w} W`, '']);
  if (s.release) out.push([s.release, '']);
  return out.map(([t, cls]) => `<span class="chip ${cls}">${t}</span>`).join('');
}

function card(p) {
  const m = mark(p.key);
  const cls = ['card'];
  if (p.is_new) cls.push('isnew');
  if (m.star) cls.push('starred');
  if (m.hide) cls.push('dimmed');
  const badges = [];
  if (p.is_new) badges.push('<span class="badge new">новинка</span>');
  if (p.price_drop > 0) badges.push(`<span class="badge drop">−${money(p.price_drop)}</span>`);
  (p.near_miss_reasons || []).forEach(r => badges.push(`<span class="badge warn">${r}</span>`));
  const stores = (p.stores || []).map(s =>
    `<a href="${s.url}" target="_blank" rel="noreferrer"><span class="st">${s.store}</span>
     <span>${money(s.price)}</span></a>`).join('');
  const parts = Object.entries(p.score_parts || {})
    .map(([k, v]) => `${k}: ${v > 0 ? '+' : ''}${v}`).join(', ');
  return `<div class="${cls.join(' ')}" data-key="${p.key}">
    ${p.image ? `<img class="thumb" loading="lazy" src="${p.image}" alt="">` : ''}
    <div class="acts">
      <button data-act="star" class="${m.star ? 'on' : ''}" title="В избранное">★</button>
      <button data-act="hide" title="Скрыть">✕</button>
    </div>
    <div class="body">
      <div class="row"><span class="name">${p.title}</span><span class="price">${money(p.price)}</span></div>
      ${badges.length ? `<div class="badges">${badges.join('')}</div>` : ''}
      <div class="specs">${chips(p)}</div>
      <div class="stores">${stores}</div>
      ${p.unknown_labels && p.unknown_labels.length
        ? `<div class="chip warn">не подтверждено: ${p.unknown_labels.join(', ')}</div>` : ''}
      <details class="why"><summary>рейтинг ${p.score}</summary><div>${parts}
        ${p.spec.sources && p.spec.sources.length ? `<br>характеристики: ${p.spec.sources.join(', ')}` : ''}
      </div></details>
      <div class="foot">
        <a class="open" href="${p.url}" target="_blank" rel="noreferrer">Открыть</a>
        <span class="score"><span class="bar"><i style="width:${p.score}%"></i></span>${p.score}</span>
      </div>
    </div></div>`;
}

function soonCard(p) {
  const m = mark(p.key);
  const cls = ['card'];
  if (m.star) cls.push('starred');
  if (m.hide) cls.push('dimmed');
  const links = (p.articles || []).map(a =>
    `<a href="${a.url}" target="_blank" rel="noreferrer"><span class="st">${a.source}</span>
     <span>→</span></a>`).join('');
  return `<div class="${cls.join(' ')}" data-key="${p.key}">
    <div class="acts">
      <button data-act="star" class="${m.star ? 'on' : ''}" title="В избранное">★</button>
      <button data-act="hide" title="Скрыть">✕</button>
    </div>
    <div class="body">
      <div class="row"><span class="name" style="padding-right:70px">${p.title}</span></div>
      <div class="badges"><span class="badge warn">ещё не в продаже</span>
        <span class="chip">${p.expected ? 'ожидается: ' + p.expected : 'срок не назван'}</span></div>
      <div class="specs">${chips(p)}</div>
      <div class="stores">${links}</div>
      ${p.unknown_labels && p.unknown_labels.length
        ? `<div class="chip warn">пока не названо: ${p.unknown_labels.join(', ')}</div>` : ''}
      <div class="foot">
        <a class="open" href="${(p.articles[0] || {}).url || '#'}" target="_blank"
           rel="noreferrer">Читать</a>
        <span class="score">${(p.spec.sources || []).join(', ')}</span>
      </div>
    </div></div>`;
}

function aliCard(p) {
  const m = mark(p.key);
  const cls = ['card'];
  if (m.star) cls.push('starred');
  if (m.hide) cls.push('dimmed');
  const link = (p.articles[0] || {}).url || '#';
  return `<div class="${cls.join(' ')}" data-key="${p.key}">
    ${p.image ? `<img class="thumb" loading="lazy" src="${p.image}" alt="">` : ''}
    <div class="acts">
      <button data-act="star" class="${m.star ? 'on' : ''}" title="В избранное">★</button>
      <button data-act="hide" title="Скрыть">✕</button>
    </div>
    <div class="body">
      <div class="row"><span class="name">${p.title}</span>
        <span class="price">${money(p.price)}</span></div>
      <div class="badges"><span class="badge warn">${p.expected || 'AliExpress'}</span>
        ${p.verified ? '<span class="badge new">характеристики из магазина</span>'
                     : '<span class="chip">характеристики со слов продавца</span>'}</div>
      <div class="specs">${chips(p)}</div>
      <div class="stores"><a href="${link}" target="_blank" rel="noreferrer">
        <span class="st">${(p.seller_title || '').slice(0, 90)}</span><span>→</span></a></div>
      ${p.unknown_labels && p.unknown_labels.length
        ? `<div class="chip warn">не указано: ${p.unknown_labels.join(', ')}</div>` : ''}
      <div class="foot">
        <a class="open" href="${link}" target="_blank" rel="noreferrer">Открыть</a>
        <span class="score">${(p.spec.sources || []).join(', ')}</span>
      </div>
    </div></div>`;
}

function visible() {
  const q = (el('q').value || '').trim().toLowerCase();
  const brand = el('brand').value;
  const pmax = +el('pmax').value;
  const onlyNew = el('only-new').checked;
  const onlySure = el('only-sure').checked;
  return DATA.filter(p => {
    const m = mark(p.key);
    if (tab === 'star') { if (!m.star) return false; }
    else if (tab === 'hidden') { if (!m.hide) return false; }
    else {
      if (m.hide) return false;
      if (tab === 'match' && p.near_miss) return false;
      if (tab === 'near' && !p.near_miss) return false;
    }
    if (q && !p.search.includes(q)) return false;
    if (brand && p.brand !== brand) return false;
    if (pmax && p.price && p.price > pmax) return false;
    if (onlyNew && !p.is_new) return false;
    if (onlySure && (p.unknown_labels || []).length) return false;
    return true;
  });
}

const SORTS = {
  score: (a, b) => b.score - a.score,
  price: (a, b) => (a.price || 1e9) - (b.price || 1e9),
  'price-desc': (a, b) => (b.price || 0) - (a.price || 0),
  battery: (a, b) => (b.spec.battery_mah || b.spec.battery_min || 0)
                     - (a.spec.battery_mah || a.spec.battery_min || 0),
  zoom: (a, b) => (b.spec.optical_zoom || 0) - (a.spec.optical_zoom || 0),
  new: (a, b) => (b.is_new ? 1 : 0) - (a.is_new ? 1 : 0) || b.score - a.score,
};

function counts() {
  const n = { match: 0, near: 0, star: 0, hidden: 0, soon: SOON.length, ali: ALI.length };
  DATA.forEach(p => {
    const m = mark(p.key);
    if (m.star) n.star++;
    if (m.hide) { n.hidden++; return; }
    if (p.near_miss) n.near++; else n.match++;
  });
  Object.entries(n).forEach(([k, v]) => el('n-' + k).textContent = v);
}

function render() {
  if (tab === 'soon' || tab === 'ali') {
    const list = tab === 'soon' ? SOON : ALI;
    const draw = tab === 'soon' ? soonCard : aliCard;
    const query = (el('q').value || '').trim().toLowerCase();
    const shown = list.filter(p => !query || p.search.includes(query))
                      .filter(p => !mark(p.key).hide);
    el('grid').innerHTML = shown.map(draw).join('');
    el('empty').hidden = shown.length > 0;
    counts();
    return;
  }
  if (false) {
    const q = (el('q').value || '').trim().toLowerCase();
    const items = SOON.filter(p => !q || p.search.includes(q))
                      .filter(p => !mark(p.key).hide);
    el('grid').innerHTML = items.map(soonCard).join('');
    el('empty').hidden = items.length > 0;
    counts();
    return;
  }
  const items = visible().sort(SORTS[el('sort').value] || SORTS.score);
  el('grid').innerHTML = items.map(card).join('');
  el('empty').hidden = items.length > 0;
  el('pmax-v').textContent = money(+el('pmax').value);
  counts();
}

el('grid').addEventListener('click', ev => {
  const button = ev.target.closest('button[data-act]');
  if (!button) return;
  const key = button.closest('.card').dataset.key;
  const state = marks[key] = marks[key] || {};
  const act = button.dataset.act;
  state[act === 'star' ? 'star' : 'hide'] = !state[act === 'star' ? 'star' : 'hide'];
  saveMarks(); render();
});

document.querySelectorAll('.tab').forEach(button =>
  button.addEventListener('click', () => {
    tab = prefs.tab = button.dataset.tab;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b === button));
    savePrefs(); render();
  }));

['q', 'sort', 'brand', 'pmax', 'only-new', 'only-sure'].forEach(id =>
  el(id).addEventListener('input', () => {
    prefs[id] = el(id).type === 'checkbox' ? el(id).checked : el(id).value;
    savePrefs(); render();
  }));

[...new Set(DATA.map(p => p.brand).filter(Boolean))].sort().forEach(b => {
  const option = document.createElement('option');
  option.value = option.textContent = b;
  el('brand').append(option);
});

['q', 'sort', 'brand', 'pmax'].forEach(id => { if (prefs[id] != null) el(id).value = prefs[id]; });
el('only-new').checked = !!prefs['only-new'];
el('only-sure').checked = !!prefs['only-sure'];
document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
try {
  render();
} catch (err) {
  // Пустая страница без объяснений — худший из возможных отчётов.
  el('empty').hidden = false;
  el('empty').textContent = 'Отчёт не отрисовался: ' + err.message;
}
</script>
</body>
</html>
"""

UNKNOWN_LABEL = {
    "battery": "батарея",
    "ip": "IP",
    "ois": "стабилизация",
    "zoom": "зум",
    "ai": "AI",
}


def _card_data(phone: Phone, meta: dict) -> dict:
    stores = []
    for offer in sorted(phone.new_offers, key=lambda o: (o.price is None, o.price or 0)):
        if any(s["store"] == offer.store for s in stores):
            continue
        stores.append({"store": offer.store, "price": offer.price, "url": offer.url})

    best = phone.best
    search = " ".join(str(p) for p in (
        phone.title, phone.brand, phone.name, phone.spec.os,
        " ".join(phone.spec.ai), " ".join(s["store"] for s in stores),
    ) if p).lower()

    return {
        "key": phone.key,
        "title": phone.title,
        "brand": phone.brand,
        "url": best.url if best else (phone.offers[0].url if phone.offers else "#"),
        "price": phone.price,
        "image": phone.image,
        "stores": stores,
        "spec": phone.spec.to_dict(),
        "score": phone.score,
        "score_parts": phone.score_parts,
        "near_miss": phone.near_miss,
        "near_miss_reasons": phone.near_miss_reasons,
        "unknown_labels": [UNKNOWN_LABEL.get(u, u) for u in phone.unknown],
        "is_new": meta.get("is_new", False) or meta.get("newly_matched", False),
        "price_drop": meta.get("price_drop", 0),
        # Историю цен в карточку не кладём: бейджа «−300 zł» достаточно, а
        # полный ряд по каждой модели раздувал бы файл отчёта на пустом месте.
        "search": search,
    }


def _soon_data(phone: Phone) -> dict:
    """Карточка вкладки «Скоро»: цены нет, зато есть срок и ссылки на статьи."""
    return {
        "key": phone.key,
        "title": phone.title,
        "brand": phone.brand,
        "expected": phone.expected,
        "spec": phone.spec.to_dict(),
        "articles": phone.articles,
        "unknown_labels": [UNKNOWN_LABEL.get(u, u) for u in phone.unknown],
        "search": " ".join(str(x) for x in (phone.title, phone.brand, phone.expected,
                                            " ".join(a["source"] for a in phone.articles))
                           if x).lower(),
    }


def _ali_data(phone: Phone) -> dict:
    """
    Карточка вкладки «AliExpress».

    `verified` — важное различие: характеристики взяты из таблицы магазина
    (модель продаётся и в Польше) или только со слов продавца.
    """
    return {
        "key": "ali:" + phone.key,
        "title": phone.title,
        "brand": phone.brand,
        "price": phone.ali_price,
        "image": phone.ali_image,
        "expected": phone.expected,
        "seller_title": phone.ali_title,
        "spec": phone.spec.to_dict(),
        "articles": phone.articles,
        "verified": any(s != "aliexpress" for s in phone.spec.sources),
        "unknown_labels": [UNKNOWN_LABEL.get(u, u) for u in phone.unknown],
        "search": " ".join(str(x) for x in (phone.title, phone.brand, phone.ali_title)
                           if x).lower(),
    }


def render(path: Path, phones: list[tuple[Phone, dict]], offers_seen: int, cfg: dict,
           spec_stats: dict | None = None, soon: list[Phone] | None = None,
           ali: list[Phone] | None = None) -> Path:
    data = [_card_data(p, m) for p, m in phones]
    cr = cfg["criteria"]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    matched = sum(1 for c in data if not c["near_miss"])
    fresh = sum(1 for c in data if c["is_new"] and not c["near_miss"])
    stats = spec_stats or {}
    sources = ", ".join(SOURCE_LABEL[name] for name, conf in cfg["sources"].items()
                        if conf.get("enabled"))
    # offers_seen == 0 — это перерисовка из базы (команда `report`), а не прогон:
    # счётчики предложений и загруженных карточек в этом случае врали бы нулями.
    scan = (f" · просмотрено {offers_seen} предложений" if offers_seen
            else " · перерисовано из базы, без опроса магазинов")
    counters = (f"характеристик из кеша {stats.get('cached', 0)}, "
                f"загружено карточек {stats.get('fetched', 0)} · " if stats else "")
    # Воронка по батарее: иначе непонятно, почему при полке, полной телефонов
    # с большой батареей, в выдаче пусто (обычно — камера не подтверждена).
    funnel = (f"по батарее прошло {stats['battery_ok']} моделей, "
              f"камера известна у {stats.get('camera_known', 0)}<br>"
              if stats.get("battery_ok") is not None else "")
    subtitle = (
        f"{matched} подходит · {fresh} новых · {len(data) - matched} почти{scan}<br>"
        f"батарея от {cr.get('battery_mah_min')} мАч · зум от "
        f"{cr.get('optical_zoom_min')}× · {cr.get('ip_min')} · OIS · AI · "
        f"до {cr.get('price_max')} zł<br>"
        + funnel +
        f"{sources} · {counters}обновлено {now}"
        + (f"<br>скоро выйдет: {len(soon)} моделей" if soon else "")
        + (f" · на AliExpress: {len(ali)}" if ali else "")
    )

    html = (
        TEMPLATE.replace("__TITLE__", now)
        .replace("__SUBTITLE__", subtitle)
        .replace("__MAXPRICE__", str(cr.get("price_max") or 20000))
        .replace("__BATTERY__", str(cr.get("battery_mah_min") or 0))
        .replace("__ZOOM__", str(cr.get("optical_zoom_min") or 0))
        .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__SOON__", json.dumps([_soon_data(p) for p in (soon or [])],
                                        ensure_ascii=False).replace("</", "<\\/"))
        .replace("__ALI__", json.dumps([_ali_data(p) for p in (ali or [])],
                                      ensure_ascii=False).replace("</", "<\\/"))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
