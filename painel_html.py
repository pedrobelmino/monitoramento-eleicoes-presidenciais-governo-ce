#!/usr/bin/env python3
"""Painel das Eleições 2026 — HTML responsivo com abas e atualização por JSON.

Duas abas:
  1) "País, Ceará e Estados" — Presidente (70%) + governos de CE e SP (30%)
  2) "Mapa dos governadores" — Brasil pintado por lado do partido

O refresh de 60 s busca apenas `/dados.json` e reescreve os valores na tela
(marcados com data-c / data-w). A página nunca recarrega: o mapa, a aba aberta e
a posição de rolagem continuam onde estão.

Uso:  python painel_html.py [arquivo.html]
"""
from __future__ import annotations

import base64
import html
import io
from datetime import datetime

# cores dos três blocos na ordem pedida: 1) Sul+SE+CO  2) Nordeste  3) Norte
CORES_BLOCO = ["#58a6ff", "#e3b341", "#3fb950"]

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%;height:100%}
body{height:100vh;overflow:hidden;background:#0f1419;color:#e6edf3;
 font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
 font-size:clamp(13px,0.9vw,16px);display:flex;flex-direction:column;gap:10px;
 padding:clamp(6px,0.7vw,14px)}
.topo{background:#1a2029;border:1px solid #2a3441;border-radius:12px;
 padding:10px 16px;display:flex;justify-content:space-between;align-items:center;
 gap:14px;flex-wrap:wrap}
.topo h1{font-size:1.15em;font-weight:650;letter-spacing:.01em}
.topo .meta{color:#8b98a5;font-size:.8em;display:flex;align-items:center;gap:8px}
.pulso{width:9px;height:9px;border-radius:50%;background:#3fb950;
 animation:p 1.6s infinite;flex:0 0 auto}
@keyframes p{0%,100%{opacity:1}50%{opacity:.2}}
.abas{display:flex;gap:6px;flex-wrap:wrap}
.aba{background:#161b22;border:1px solid #2a3441;color:#c9d1d9;border-radius:10px;
 padding:9px 16px;font:inherit;font-size:.92em;font-weight:600;cursor:pointer;
 transition:.15s}
.aba:hover{border-color:#3d4759;color:#e6edf3}
.aba[aria-selected="true"]{background:#1f6feb22;border-color:#1f6feb;color:#79c0ff}
.tela{display:none;flex-direction:column;gap:10px}
.tela[data-ativa="1"]{display:flex;flex:1;min-height:0}
.tela[data-ativa="1"]#painel{overflow-y:auto}   /* a aba do painel rola normalmente */
#mapa[data-ativa="1"]{overflow:hidden}          /* a aba do mapa cabe na tela */
.grade{display:grid;grid-template-columns:70fr 30fr;gap:10px;align-items:stretch}
.col{display:flex;flex-direction:column;gap:10px;min-width:0}
.corrida{background:#161b22;border:1px solid #2a3441;border-radius:12px;overflow:hidden}
.corrida>header{padding:10px 16px;border-bottom:1px solid #21262d;
 display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.corrida>header .titulo{font-weight:650;font-size:1.05em;
 border-left:5px solid #eb6e1e;padding-left:10px}
.corrida>header .info{color:#8b98a5;font-size:.78em;
 font-variant-numeric:tabular-nums;text-align:right}
.linha{display:flex;align-items:center;gap:12px;padding:9px 16px;
 border-bottom:1px solid #1d232b}
.linha:last-child{border-bottom:none}
.linha img,.linha .vazio{width:clamp(38px,3vw,58px);height:clamp(38px,3vw,58px);
 border-radius:50%;object-fit:cover;background:#21262d;flex:0 0 auto}
.linha .quem{flex:1;min-width:0}
.linha .nome{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.linha .partido{color:#8b98a5;font-size:.8em}
.linha .votos{color:#c9d1d9;font-size:.82em;font-variant-numeric:tabular-nums}
.linha .pct{font-weight:700;font-size:1.5em;color:#eb6e1e;
 font-variant-numeric:tabular-nums;flex:0 0 auto}
.mini{height:5px;background:#21262d;border-radius:3px;margin-top:6px;overflow:hidden}
.mini span{display:block;height:100%;background:#eb6e1e;border-radius:3px;
 transition:width .6s ease}
.barra span{transition:width .6s ease}
.regioes{padding:10px 16px 14px}
.regioes h2{font-size:.85em;color:#58a6ff;text-transform:uppercase;
 letter-spacing:.06em;margin-bottom:2px}
.regioes .dica{color:#8b98a5;font-size:.72em;margin-bottom:10px}
.reg{display:grid;grid-template-columns:minmax(96px,190px) 1fr auto;
 gap:10px;align-items:center;margin-bottom:7px}
.reg .n{font-weight:600;font-size:.88em}
.reg .n small{display:block;color:#8b98a5;font-weight:400;font-size:.78em;
 font-variant-numeric:tabular-nums}
.barra{height:20px;background:#21262d;border-radius:5px;overflow:hidden}
.barra span{display:block;height:100%;border-radius:5px}
.reg .pct{font-weight:700;font-variant-numeric:tabular-nums;text-align:right;
 font-size:.95em}
.reg .pct small{display:block;color:#8b98a5;font-weight:400;font-size:.75em}
.reg .quem-bloco{grid-column:1/-1;color:#c9d1d9;font-size:.8em;padding:2px 0 6px}
.reg .quem-bloco b{color:#e6edf3}
.cmp{margin:0 16px 14px;background:#1a2029;border-left:5px solid #3c485a;
 border-radius:8px;padding:11px 14px}
.cmp h3{font-size:.78em;color:#58a6ff;text-transform:uppercase;
 letter-spacing:.05em;margin-bottom:9px}
.cmp table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.cmp th,.cmp td{text-align:right;padding:3px 0;font-size:.92em}
.cmp th:first-child,.cmp td:first-child{text-align:left;color:#8b98a5;
 font-weight:400;font-size:.82em}
.cmp thead th{color:#8b98a5;font-size:.74em;text-transform:uppercase;
 letter-spacing:.04em;font-weight:600}
.cmp .agora td{color:#e6edf3;font-weight:700;font-size:1.08em}
.cmp .antes td{color:#8b98a5}
.cmp tfoot td{padding-top:7px;color:#6e7b8b;font-size:.72em;text-align:left}
.rodape{background:#1a2029;border:1px solid #2a3441;border-radius:12px;
 padding:9px 16px;color:#8b98a5;font-size:.75em;text-align:center}
.erro{color:#f85149}
/* ---- aba do mapa ---- */
.mapa-caixa{background:#161b22;border:1px solid #2a3441;border-radius:12px;
 overflow:hidden;display:flex;flex-direction:column;flex:1;min-height:0}
.mapa-caixa>header{padding:10px 16px;border-bottom:1px solid #21262d;
 display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.mapa-caixa .titulo{font-weight:650;font-size:1.05em;
 border-left:5px solid #eb6e1e;padding-left:10px}
.mapa-caixa .info{color:#8b98a5;font-size:.78em;text-align:right}
.mapa-zoom{flex:1;min-height:0;overflow:hidden;background:#0d1117;border-radius:8px;
 display:flex;align-items:center;justify-content:center;cursor:grab;
 touch-action:none;position:relative}
.mapa-zoom.arrastando{cursor:grabbing}
.mapa{width:100%;height:100%;object-fit:contain;display:block;
 transform-origin:center center;user-select:none;-webkit-user-drag:none}
.zoom-barra{display:flex;align-items:center;gap:8px;padding-top:2px;font-size:.78em;
 color:#8b98a5}
.zoom-barra button{background:#161b22;border:1px solid #2a3441;color:#c9d1d9;
 border-radius:7px;width:30px;height:26px;font:inherit;font-size:1.05em;cursor:pointer;
 line-height:1}
.zoom-barra button:hover{border-color:#3d4759;color:#e6edf3}
.zoom-barra #z-nivel{min-width:44px;text-align:center;font-variant-numeric:tabular-nums}
.zoom-barra .dica-zoom{margin-left:auto;font-size:.92em}
.legenda{display:flex;flex-wrap:wrap;gap:8px 14px;padding:10px 2px 0;
 font-size:.78em;color:#c9d1d9}
.legenda span{display:flex;align-items:center;gap:7px}
.legenda i{width:15px;height:15px;border-radius:3px;display:inline-block}
/* mapa à esquerda, lista de eleitos à direita */
.mapa-grade{display:grid;grid-template-columns:minmax(0,1.9fr) minmax(240px,0.85fr);
 gap:16px;padding:12px 16px 14px;align-items:stretch;flex:1;min-height:0}
.mapa-lado{min-width:0;min-height:0;display:flex;flex-direction:column;gap:6px}
.eleitos{min-width:0;min-height:0;overflow-y:auto}
.eleitos h2{font-size:.82em;color:#58a6ff;text-transform:uppercase;
 letter-spacing:.06em;margin-bottom:10px}
.reg-bloco{margin-bottom:11px}
.reg-bloco h3{font-size:.72em;color:#8b98a5;text-transform:uppercase;
 letter-spacing:.07em;font-weight:700;border-bottom:1px solid #21262d;
 padding-bottom:3px;margin-bottom:5px;display:flex;justify-content:space-between}
.reg-bloco h3 span{color:#6e7b8b}
.reg-bloco ul{list-style:none}
.reg-bloco li{font-size:.84em;display:flex;gap:7px;align-items:baseline;
 padding:2px 0}
.reg-bloco b{color:#8b98a5;min-width:24px}
.reg-bloco .pt{color:#6e7b8b;font-size:.85em}
.reg-bloco .pc{margin-left:auto;font-weight:700;font-variant-numeric:tabular-nums}
.ressalva{padding:0 16px 16px;color:#6e7b8b;font-size:.75em;line-height:1.5}
/* ---- aba 3: cargos proporcionais ---- */
.prop-grade{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;
 flex:1;min-height:0}
.prop-col{background:#161b22;border:1px solid #2a3441;border-radius:12px;
 display:flex;flex-direction:column;min-height:0;overflow:hidden}
.prop-col>header{padding:9px 14px;border-bottom:1px solid #21262d;flex:0 0 auto}
.prop-col .titulo{font-weight:650;font-size:.95em;border-left:5px solid #eb6e1e;
 padding-left:9px}
.prop-col .info{color:#8b98a5;font-size:.7em;margin-top:4px;
 font-variant-numeric:tabular-nums}
.prop-lista{flex:1 1 auto;min-height:0;overflow-y:auto}
.linha-prop{display:flex;align-items:center;gap:9px;padding:5px 14px;
 border-bottom:1px solid #1a2029}
.linha-prop img,.linha-prop .vazio{width:34px;height:34px;border-radius:50%;
 object-fit:cover;background:#21262d;flex:0 0 auto}
.linha-prop .quem{flex:1;min-width:0}
.linha-prop .nome{font-weight:600;font-size:.82em;white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis}
.linha-prop .partido{color:#8b98a5;font-size:.7em}
.linha-prop .votos{color:#8b98a5;font-size:.68em;font-variant-numeric:tabular-nums;
 text-align:right;flex:0 0 auto}
.linha-prop .pct{font-weight:700;font-size:.92em;color:#eb6e1e;
 font-variant-numeric:tabular-nums;min-width:56px;text-align:right;flex:0 0 auto}
.linha-prop .selo{flex:0 0 auto;font-size:.6em;font-weight:700;letter-spacing:.04em;
 color:#0d1117;background:#3fb950;border-radius:4px;padding:2px 5px;text-transform:uppercase}
.projecao{border-top:1px solid #2a3441;background:#0f1720;padding:7px 14px;flex:0 0 auto}
.projecao .linha1{display:flex;gap:6px;align-items:center;margin-bottom:5px;flex-wrap:wrap}
.projecao .tag{font-size:.62em;color:#8b98a5;background:#1a2029;border:1px solid #2a3441;
 border-radius:4px;padding:2px 6px;font-variant-numeric:tabular-nums}
.projecao .tag b{color:#c9d1d9}
.projecao .cadeiras{display:flex;flex-wrap:wrap;gap:4px}
.projecao .cadeiras span{font-size:.66em;background:#1f2937;border:1px solid #30363d;
 border-radius:4px;padding:2px 6px;font-weight:600}
.prop-eleitos{border-top:1px solid #2a3441;background:#0e1a12;padding:7px 14px 9px;
 flex:0 1 auto;min-height:0;overflow-y:auto}
.prop-eleitos h3{font-size:.66em;color:#3fb950;text-transform:uppercase;letter-spacing:.06em;
 margin-bottom:6px;position:sticky;top:0}
.prop-eleitos h3 .conta{color:#8b98a5;font-weight:400}
.prop-eleitos .chips{display:flex;flex-wrap:wrap;gap:4px}
.prop-eleitos .chips span{font-size:.66em;background:#12261a;border:1px solid #2ea0434d;
 border-radius:4px;padding:2px 6px;line-height:1.35}
.prop-eleitos .chips b{color:#3fb950;font-weight:700}
.prop-partidos{border-top:1px solid #2a3441;padding:8px 14px 11px;flex:0 0 auto;
 background:#12171e}
.prop-partidos h3{font-size:.66em;color:#58a6ff;text-transform:uppercase;
 letter-spacing:.06em;margin-bottom:6px}
.part-linha{display:grid;grid-template-columns:44px 1fr 46px;gap:7px;
 align-items:center;margin-bottom:3px;font-size:.72em}
.part-linha .sig{color:#c9d1d9;font-weight:600;font-variant-numeric:tabular-nums}
.part-linha .barra{height:7px;background:#21262d;border-radius:3px;overflow:hidden}
.part-linha .barra span{display:block;height:100%;background:#58a6ff;border-radius:3px}
.part-linha .val{text-align:right;color:#8b98a5;font-variant-numeric:tabular-nums}
@media (max-width:1100px){
 .prop-grade{grid-template-columns:1fr;overflow-y:auto}
 .prop-col{max-height:none}
}
.vazio-msg{padding:14px 16px;color:#8b98a5}
/* em tela larga, os dois governos preenchem toda a altura ao lado do presidente */
/* Coluna estreita: cinco governos dividem a altura. Os tamanhos acompanham a
   altura da tela (vh), assim as cinco linhas cabem tanto em 900px quanto em
   1440px de altura — sem cortar conteúdo nem criar barra de rolagem. */
.col-gov .corrida>header{padding:.45vh 13px}
.col-gov .corrida>header .titulo{font-size:clamp(.72em,1.55vh,.95em);
 border-left-width:4px;padding-left:8px}
.col-gov .corrida>header .info{font-size:clamp(.55em,1.1vh,.7em);line-height:1.25}
.col-gov .linha{padding:.4vh 13px;gap:8px}
.col-gov .linha img,.col-gov .linha .vazio{
 width:clamp(18px,4vh,44px);height:clamp(18px,4vh,44px)}
.col-gov .linha .nome{font-size:clamp(.68em,1.45vh,.95em)}
.col-gov .linha .partido{font-size:clamp(.56em,1.15vh,.78em)}
.col-gov .linha .pct{font-size:clamp(.8em,2.2vh,1.35em)}
/* o acessório sai: o essencial (nome, partido, percentual) fica */
.col-gov .linha .votos,.col-gov .mini{display:none}
@media (min-width:1101px){
 .col-gov{align-self:stretch}
 .col-gov .corrida{flex:1 1 0;display:flex;flex-direction:column;min-height:0}
 .col-gov .corrida>header{flex:0 0 auto}
 .col-gov .corrida>.linha{flex:1 1 0;align-items:center;overflow:hidden}
}
@media (max-width:1100px){
 .grade{grid-template-columns:1fr}
 .reg{grid-template-columns:minmax(84px,1fr) 2fr auto}
}
@media (max-width:620px){
 .topo h1{font-size:1em}
 .linha .pct{font-size:1.25em}
 .reg{grid-template-columns:1fr;gap:3px}
 .reg .pct{text-align:left}
}
"""

JS = """
const nf = new Intl.NumberFormat('pt-BR');
const nf2 = new Intl.NumberFormat('pt-BR',
  {minimumFractionDigits:2, maximumFractionDigits:2});

function pega(o, caminho){
  return caminho.split('.').reduce((a, k) =>
    (a === null || a === undefined) ? undefined : a[k], o);
}
function formata(v, tipo){
  if (v === null || v === undefined) return '\\u2014';
  if (tipo === 'num') return nf.format(v);
  if (tipo === 'pct') return nf2.format(v);
  return String(v);
}
function aplica(d){
  document.querySelectorAll('[data-c]').forEach(el => {
    const v = pega(d, el.dataset.c);
    if (v !== undefined) el.textContent = formata(v, el.dataset.f);
  });
  document.querySelectorAll('[data-w]').forEach(el => {
    const v = Number(pega(d, el.dataset.w));
    if (!isNaN(v)) el.style.width = Math.max(0, Math.min(100, v)) + '%';
  });
  const ul = document.getElementById('lista-eleitos');
  if (ul && d.eleitos){
    const ordem = ["Sul", "Sudeste", "Centro-Oeste", "Nordeste", "Norte"];
    const grupos = {};
    d.eleitos.forEach(g => {
      const r = g.regiao || '\u2014';
      (grupos[r] = grupos[r] || []).push(g);
    });
    ul.innerHTML = ordem.concat(Object.keys(grupos).filter(r => ordem.indexOf(r) < 0))
      .filter(r => grupos[r]).map(r => {
        const itens = grupos[r].slice().sort((a, b) => b.pct - a.pct).map(g =>
          '<li><b>' + g.uf + '</b> ' + g.nome +
          ' <span class="pt">' + g.partido + '</span>' +
          '<span class="pc" style="color:' + g.cor + '">' +
          nf2.format(g.pct) + '%</span></li>').join('');
        return '<section class="reg-bloco"><h3>' + r +
          '<span>' + grupos[r].length + '</span></h3><ul>' + itens + '</ul></section>';
      }).join('');
    const h = document.getElementById('titulo-eleitos');
    if (h) h.textContent = 'Já eleitos no 1º turno (' + d.eleitos.length + ')';
  }
  if (d.prop){
    Object.keys(d.prop).forEach(chave => {
      const col = document.querySelector('[data-prop="' + chave + '"]');
      if (!col) return;
      const p = d.prop[chave];
      const lista = col.querySelector('.prop-lista');
      if (lista){
        lista.innerHTML = (p.mais_votados || []).map(c =>
          '<div class="linha-prop">' +
          (c.foto ? '<img src="' + c.foto + '" alt="">' : '<div class="vazio"></div>') +
          '<div class="quem"><div class="nome">' + c.nome + '</div>' +
          '<div class="partido">' + c.partido + '</div></div>' +
          (c.projetado ? '<span class="selo">eleito</span>' : '') +
          '<div class="votos">' + nf.format(c.votos) + '</div>' +
          '<div class="pct">' + nf2.format(c.pct) + '%</div></div>').join('');
      }
      const pt = col.querySelector('.prop-partidos');
      if (pt){
        pt.innerHTML = '<h3>Votação somada por partido</h3>' +
          (p.partidos || []).map(x =>
            '<div class="part-linha"><span class="sig">' + x.partido + '</span>' +
            '<div class="barra"><span style="width:' +
            Math.max(0, Math.min(100, x.pct)) + '%"></span></div>' +
            '<span class="val">' + nf2.format(x.pct) + '%</span></div>').join('');
      }
      const fx = col.querySelector('.projecao');
      if (fx){
        let t = '<div class="linha1">';
        if (p.vagas) t += '<span class="tag"><b>' + p.vagas + '</b> vagas</span>';
        if (p.quociente) t += '<span class="tag">quociente <b>' + nf.format(Math.round(p.quociente)) + '</b></span>';
        if (p.piso) t += '<span class="tag">piso <b>' + nf.format(Math.round(p.piso)) + '</b></span>';
        t += '<span class="tag">projeção: <b>' + (p.projetados || 0) + '</b> eleitos</span></div>';
        if (p.grupos && p.grupos.length){
          t += '<div class="cadeiras">' + p.grupos.map(g =>
            '<span>' + g.grupo + ' <b>' + g.cadeiras + '</b></span>').join('') + '</div>';
        }
        fx.innerHTML = t;
      }
      const ce = col.querySelector('.prop-eleitos');
      if (ce){
        const n = (p.eleitos_lista || []).length;
        ce.innerHTML = '<h3>Prováveis eleitos <span class="conta">' + n + '</span></h3>' +
          '<div class="chips">' + (p.eleitos_lista || []).map(x =>
            '<span>' + x.nome + ' <b>' + x.partido + '</b></span>').join('') + '</div>';
      }
      const ap = col.querySelector('.prop-apurado');
      if (ap) ap.textContent = nf2.format(p.pct_apurado);
    });
  }
  const q = document.getElementById('quando');
  if (q && d.gerado) q.textContent = 'dados de ' + d.gerado;
}
async function atualiza(){
  const pulso = document.getElementById('pulso');
  try{
    const r = await fetch('/dados.json?t=' + Date.now(), {cache:'no-store'});
    if (!r.ok) return;
    aplica(await r.json());
    if (pulso) pulso.style.background = '#3fb950';
  }catch(e){
    if (pulso) pulso.style.background = '#f85149';   // mantém os últimos dados
  }
}
/* ---- zoom apenas no mapa (a página não mexe) ---- */
let zEsc = 1, zX = 0, zY = 0, arrastando = false, ax = 0, ay = 0;
function pintaZoom(){
  const img = document.getElementById('mapa-img');
  if (!img) return;
  img.style.transform = 'translate(' + zX + 'px,' + zY + 'px) scale(' + zEsc + ')';
  const n = document.getElementById('z-nivel');
  if (n) n.textContent = Math.round(zEsc * 100) + '%';
}
function defineZoom(v){
  zEsc = Math.min(10, Math.max(1, v));
  if (zEsc === 1){ zX = 0; zY = 0; }
  pintaZoom();
}
const areaZoom = document.getElementById('mapa-zoom');
if (areaZoom){
  areaZoom.addEventListener('wheel', e => {
    e.preventDefault();
    const antes = zEsc;
    defineZoom(zEsc * (e.deltaY < 0 ? 1.18 : 1 / 1.18));
    if (zEsc > 1 && zEsc !== antes){
      const r = areaZoom.getBoundingClientRect();
      const cx = e.clientX - r.left - r.width / 2;
      const cy = e.clientY - r.top - r.height / 2;
      zX -= cx * (zEsc / antes - 1);
      zY -= cy * (zEsc / antes - 1);
      pintaZoom();
    }
  }, {passive: false});
  areaZoom.addEventListener('pointerdown', e => {
    arrastando = true;
    ax = e.clientX - zX; ay = e.clientY - zY;
    areaZoom.classList.add('arrastando');
    try { areaZoom.setPointerCapture(e.pointerId); } catch(err){}
  });
  areaZoom.addEventListener('pointermove', e => {
    if (!arrastando) return;
    zX = e.clientX - ax; zY = e.clientY - ay; pintaZoom();
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(ev =>
    areaZoom.addEventListener(ev, () => {
      arrastando = false; areaZoom.classList.remove('arrastando');
    }));
}
document.querySelectorAll('.zoom-barra button').forEach(b =>
  b.addEventListener('click', () => {
    const d = b.dataset.z;
    if (d === '+') defineZoom(zEsc * 1.3);
    else if (d === '-') defineZoom(zEsc / 1.3);
    else defineZoom(1);
  }));

function abreAba(id){
  document.querySelectorAll('.aba').forEach(b =>
    b.setAttribute('aria-selected', b.dataset.alvo === id ? 'true' : 'false'));
  document.querySelectorAll('.tela').forEach(t =>
    t.setAttribute('data-ativa', t.id === id ? '1' : '0'));
  if (id === 'mapa'){
    const img = document.getElementById('mapa-img');
    if (img) img.src = '/mapa.svg?t=' + Date.now();
  }
  try { localStorage.setItem('aba', id); } catch(e){}
}
document.querySelectorAll('.aba').forEach(b =>
  b.addEventListener('click', () => abreAba(b.dataset.alvo)));
function inicia(){
  let inicial = 'painel';
  try { inicial = localStorage.getItem('aba') || 'painel'; } catch(e){}
  abreAba(inicial);
  atualiza();
  setInterval(atualiza, 60000);
}
// o script fica no fim do body: em geral o DOMContentLoaded já disparou
if (document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', inicia);
} else {
  inicia();
}
"""


def _uri(img) -> str:
    """PIL Image -> data URI (a foto vira parte do HTML, sem arquivo solto)."""
    if img is None:
        return ""
    try:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=86)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _foto_mini(img) -> str:
    """Data URI pequeno (64px) — usado nos cargos proporcionais, onde há muitos."""
    if img is None:
        return ""
    try:
        im = img.convert("RGB").copy()
        im.thumbnail((64, 64))
        lado = min(im.size)
        im = im.crop(((im.width - lado) // 2, (im.height - lado) // 2,
                      (im.width + lado) // 2, (im.height + lado) // 2))
        im = im.resize((64, 64))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _cargo_prop(chave: str, d: dict) -> str:
    """Uma coluna da aba 3: os mais votados + a votação somada por partido."""
    e = html.escape
    rotulo = e(str(d.get("rotulo", chave.upper()))) if d else chave.upper()
    if not d or d.get("erro"):
        return (f'<section class="prop-col" data-prop="{chave}"><header>'
                f'<div class="titulo">{rotulo}</div>'
                f'<div class="info">sem dados do TSE nesta rodada</div></header></section>')

    linhas = []
    for i, c in enumerate(d.get("mais_votados", [])):
        foto = _foto_mini(c.get("foto"))
        img = f'<img src="{foto}" alt="">' if foto else '<div class="vazio"></div>'
        selo = ('<span class="selo" title="na projeção atual de vagas">eleito</span>'
                if c.get("projetado") else "")
        linhas.append(
            f'<div class="linha-prop">{img}<div class="quem">'
            f'<div class="nome">{e(str(c.get("nome", "")))}</div>'
            f'<div class="partido">{e(str(c.get("partido", "")))}</div></div>'
            f'{selo}'
            f'<div class="votos"><span data-c="prop.{chave}.mais_votados.{i}.votos" '
            f'data-f="num">{_n(c.get("votos"))}</span></div>'
            f'<div class="pct"><span data-c="prop.{chave}.mais_votados.{i}.pct" '
            f'data-f="pct">{_duas(_f(c.get("pct")))}</span>%</div></div>')

    partes = []
    for p in d.get("partidos", [])[:8]:
        pc = _f(p.get("pct"))
        partes.append(
            f'<div class="part-linha"><span class="sig">{e(str(p.get("partido", "")))}</span>'
            f'<div class="barra"><span style="width:{max(0.0, min(100.0, pc)):.2f}%"></span></div>'
            f'<span class="val">{_duas(pc)}%</span></div>')

    vagas = int(d.get("vagas") or 0)
    qe = _f(d.get("quociente"))
    piso = _f(d.get("piso"))
    cadeiras = "".join(
        f'<span>{e(str(g.get("grupo", "")))} <b>{g.get("cadeiras")}</b></span>'
        for g in (d.get("grupos") or [])[:9])
    eleitos = "".join(
        f'<span>{e(str(x.get("nome", "")))} <b>{e(str(x.get("partido", "")))}</b></span>'
        for x in (d.get("eleitos_lista") or []))
    faixa = (
        f'<div class="projecao"><div class="linha1">'
        + (f'<span class="tag"><b>{vagas}</b> vagas</span>' if vagas else '')
        + (f'<span class="tag">quociente <b>{_n(round(qe))}</b></span>' if qe else '')
        + (f'<span class="tag">piso <b>{_n(round(piso))}</b></span>' if piso else '')
        + f'<span class="tag">projeção: <b>{d.get("projetados", 0)}</b> eleitos</span>'
        + '</div>'
        + (f'<div class="cadeiras">{cadeiras}</div>' if cadeiras else '')
        + '</div>')

    bloco_eleitos = (
        f'<div class="prop-eleitos"><h3>Prováveis eleitos <span class="conta">'
        f'{d.get("projetados", 0)}</span></h3><div class="chips" id="eleitos-{chave}">'
        f'{eleitos}</div></div>')

    return (f'<section class="prop-col" data-prop="{chave}"><header>'
            f'<div class="titulo">{rotulo}</div>'
            f'<div class="info"><span class="prop-apurado" '
            f'data-c="prop.{chave}.pct_apurado" data-f="pct">'
            f'{_duas(_f(d.get("pct_apurado")))}</span>% apurado · '
            f'<span data-c="prop.{chave}.validos" data-f="num">{_n(d.get("validos"))}</span> '
            f'votos válidos · <span data-c="prop.{chave}.candidatos" data-f="num">'
            f'{_n(d.get("candidatos"))}</span> candidatos</div></header>'
            f'<div class="prop-lista">{"".join(linhas)}</div>'
            f'{faixa}'
            f'{bloco_eleitos}'
            f'<div class="prop-partidos"><h3>Votação somada por partido</h3>'
            f'{"".join(partes)}</div></section>')


def _n(v) -> str:
    """1234567 -> '1.234.567'."""
    try:
        return f"{int(str(v).replace('.', '').replace(',', '')):,}".replace(",", ".")
    except Exception:
        return str(v or "—")


def _f(v) -> float:
    """0.5125 ou '51,25' -> 51.25 (float)."""
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except Exception:
        return 0.0


def _duas(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _corrida(b: dict, larga: bool, caminho: str) -> str:
    """Um bloco de corrida. `caminho` é a rota do valor dentro do /dados.json."""
    e = html.escape
    titulo = e(str(b.get("rotulo", "")))
    if b.get("erro"):
        return (f'<section class="corrida"><header><div class="titulo">{titulo}</div>'
                f'<div class="info erro">sem dados do TSE nesta rodada</div></header></section>')

    linhas = []
    for i, c in enumerate(b.get("cands", [])[:2]):
        foto = _uri(c.get("foto"))
        img = f'<img src="{foto}" alt="">' if foto else '<div class="vazio"></div>'
        linhas.append(
            f'<div class="linha">{img}<div class="quem">'
            f'<div class="nome">{e(str(c.get("nome", "")))}</div>'
            f'<div class="partido">{e(str(c.get("partido", "")))}</div>'
            f'<div class="votos"><span data-c="{caminho}.cands.{i}.votos" data-f="num">'
            f'{_n(c.get("votos"))}</span> votos</div>'
            f'<div class="mini"><span data-w="{caminho}.cands.{i}.pct" '
            f'style="width:{min(100.0, _f(c.get("pct"))):.2f}%"></span></div>'
            f'</div><div class="pct"><span data-c="{caminho}.cands.{i}.pct" data-f="pct">'
            f'{_duas(_f(c.get("pct")))}</span>%</div></div>')

    regioes = ""
    grupos = (b.get("regioes") or {}).get("_grupos") or []
    if larga and grupos:
        rs = []
        for i, g in enumerate(grupos):
            cor = CORES_BLOCO[i % len(CORES_BLOCO)]
            cands = " &nbsp;·&nbsp; ".join(
                f'<b>{e(str(c["nome"]).title())}</b> '
                f'<span data-c="{caminho}.grupos.{i}.cands.{j}.pct" data-f="pct">'
                f'{_duas(_f(c["pct"]))}</span>%'
                for j, c in enumerate(g.get("cands", [])))
            rs.append(
                f'<div class="reg"><div class="n">{e(g["grupo"])}'
                f'<small><span data-c="{caminho}.grupos.{i}.st" data-f="num">'
                f'{_n(g.get("st"))}</span> de '
                f'<span data-c="{caminho}.grupos.{i}.ts" data-f="num">'
                f'{_n(g.get("ts"))}</span> seções</small></div>'
                f'<div class="barra"><span data-w="{caminho}.grupos.{i}.pct" '
                f'style="width:{max(0.0, min(100.0, g["pct"])):.2f}%;background:{cor}"></span></div>'
                f'<div class="pct" style="color:{cor}">'
                f'<span data-c="{caminho}.grupos.{i}.pct" data-f="pct">{_duas(g["pct"])}</span>%'
                f'<small>falta <span data-c="{caminho}.grupos.{i}.falta" data-f="pct">'
                f'{_duas(max(0.0, 100.0 - g["pct"]))}</span>%</small></div>'
                f'<div class="quem-bloco">{cands}</div></div>')
        regioes = ('<div class="regioes"><h2>Urnas apuradas por bloco de regiões</h2>'
                   '<div class="dica">1) Sul + Sudeste + Centro-Oeste · 2) Nordeste · 3) Norte — '
                   'barra cheia = seções já apuradas</div>'
                   + "".join(rs) + "</div>")

    cmp_ = ""   # bloco da eleição de 2022 removido da aba 1 a pedido

    return (f'<section class="corrida" id="corrida-{caminho.replace(".", "-")}">'
            f'<header><div class="titulo">{titulo}</div>'
            f'<div class="info"><span data-c="{caminho}.pct" data-f="pct">'
            f'{_duas(_f(b.get("pct")))}</span>% apurado · '
            f'<span data-c="{caminho}.sec_tot" data-f="num">{_n(b.get("sec_tot"))}</span> de '
            f'<span data-c="{caminho}.sec_total" data-f="num">{_n(b.get("sec_total"))}</span> '
            f'seções<br>gerado {e(str(b.get("gerado", "")))}</div></header>'
            + "".join(linhas) + regioes + cmp_ + "</section>")


def _lista_eleitos(eleitos: list[dict]) -> str:
    """Lista dos já eleitos agrupada por região do país."""
    ordem = ["Sul", "Sudeste", "Centro-Oeste", "Nordeste", "Norte"]
    grupos: dict[str, list] = {}
    for g in eleitos:
        grupos.setdefault(g.get("regiao") or "—", []).append(g)
    partes = []
    for reg in ordem + [r for r in grupos if r not in ordem]:
        if reg not in grupos:
            continue
        itens = "".join(
            f'<li><b>{html.escape(g["uf"])}</b> {html.escape(g["nome"])} '
            f'<span class="pt">{html.escape(g["partido"])}</span>'
            f'<span class="pc" style="color:{g["cor"]}">{_duas(g["pct"])}%</span></li>'
            for g in sorted(grupos[reg], key=lambda x: -x["pct"]))
        partes.append(f'<section class="reg-bloco"><h3>{html.escape(reg)}'
                      f'<span>{len(grupos[reg])}</span></h3><ul>{itens}</ul></section>')
    return "".join(partes)


def gera(blocos: list[dict], titulo: str = "Apuração TSE 2026",
         governadores: list[dict] | None = None,
         proporcionais: dict | None = None) -> str:
    """Página completa: aba 1 (país/estados) + aba 2 (mapa)."""
    pres = next((b for b in blocos if b.get("regioes")), None)
    governos = [b for b in blocos if b.get("uf") in ("ce", "sp", "rs", "pb", "rj")]

    col_pres = _corrida(pres, True, "blocos.0") if pres else ""
    col_gov = "".join(_corrida(g, False, f"blocos.{1 + i}") for i, g in enumerate(governos))

    prop = proporcionais or {}
    cols_prop = "".join(_cargo_prop(k, prop.get(k)) for k in ("senador", "federal", "estadual"))

    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    govs = governadores or []
    eleitos = sorted([g for g in govs if g["vence_1t"]], key=lambda x: -x["pct"])
    itens = _lista_eleitos(eleitos)

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(titulo)}</title>
<style>{CSS}</style></head><body>
<div class="topo">
  <h1>🗳️ Apuração oficial do TSE — Eleições 2026 · 1º turno</h1>
  <div class="meta"><span class="pulso" id="pulso"></span>
    atualiza os dados a cada 60 s · <span id="quando">gerado às {agora}</span></div>
</div>
<nav class="abas">
  <button class="aba" data-alvo="painel" aria-selected="true">País, Ceará e Estados</button>
  <button class="aba" data-alvo="mapa" aria-selected="false">Mapa dos governadores</button>
  <button class="aba" data-alvo="proporcionais" aria-selected="false">Ceará — Senado e Câmara</button>
</nav>

<section class="tela" id="painel" data-ativa="1">
  <div class="grade">
    <div class="col col-pres">{col_pres}</div>
    <div class="col col-gov">{col_gov}</div>
  </div>
</section>

<section class="tela" id="mapa" data-ativa="0">
  <div class="mapa-caixa">
    <header>
      <div class="titulo">GOVERNADORES — PROJEÇÃO PARA O 1º TURNO</div>
      <div class="info">lado do partido no mapa · tom cheio = já passou de 50%</div>
    </header>
    <div class="mapa-grade">
      <div class="mapa-lado">
        <div class="mapa-zoom" id="mapa-zoom">
          <img class="mapa" id="mapa-img" alt="Mapa do Brasil com os governadores por lado">
        </div>
        <div class="zoom-barra">
          <button type="button" data-z="-" title="diminuir">−</button>
          <span id="z-nivel">100%</span>
          <button type="button" data-z="+" title="aumentar">+</button>
          <button type="button" data-z="0" title="voltar ao tamanho da tela">ajustar</button>
          <span class="dica-zoom">roda do mouse dá zoom · arraste para mover</span>
        </div>
        <div class="legenda">
          <span><i style="background:#d13438"></i> Esquerda — eleita no 1º turno</span>
          <span><i style="background:#f0a2a5"></i> Esquerda — 2º turno</span>
          <span><i style="background:#2ea043"></i> Direita — eleita no 1º turno</span>
          <span><i style="background:#9fd8ae"></i> Direita — 2º turno</span>
        </div>
      </div>
      <aside class="eleitos"><h2 id="titulo-eleitos">Já eleitos no 1º turno ({len(eleitos)})</h2>
        <div id="lista-eleitos">{itens}</div></aside>
    </div>
    <div class="ressalva">Lado classificado pelo partido do candidato — leitura política, não é
    classificação oficial do TSE. O Rio Grande do Norte não existe no mapa base e aparece sem cor.
    Passe o mouse sobre um estado para ver governador, partido e percentual.</div>
  </div>
</section>

<section class="tela" id="proporcionais" data-ativa="0">
  <div class="prop-grade">{cols_prop}</div>
  <div class="rodape">Fonte: resultados.tse.jus.br/oficial/ele2026 · cargos proporcionais ·
  a votação por partido é a base do quociente eleitoral, mas a atribuição das vagas só é
  conhecida no fim da apuração</div>
</section>

<div class="rodape">Fonte: resultados.tse.jus.br/oficial/ele2026 · F11 para tela cheia</div>
<script>{JS}</script>
</body></html>"""


def dados(blocos: list[dict], governadores: list[dict] | None = None,
          proporcionais: dict | None = None) -> dict:
    """Os mesmos valores da página, em JSON, para o refresh sem recarregar."""
    pres = next((b for b in blocos if b.get("regioes")), None)
    governos = [b for b in blocos if b.get("uf") in ("ce", "sp", "rs", "pb", "rj")]

    def um_bloco(b, larga):
        if not b or b.get("erro"):
            return {"erro": True, "uf": b.get("uf") if b else None}
        d = {
            "uf": b.get("uf"), "rotulo": b.get("rotulo"),
            "pct": _f(b.get("pct")),
            "sec_tot": _f(b.get("sec_tot")), "sec_total": _f(b.get("sec_total")),
            "cands": [{"nome": c.get("nome"), "pct": _f(c.get("pct")),
                       "votos": _f(c.get("votos"))}
                      for c in b.get("cands", [])[:2]],
        }
        if larga:
            grupos = []
            for g in (b.get("regioes") or {}).get("_grupos", []):
                grupos.append({
                    "grupo": g["grupo"], "pct": g["pct"], "st": g["st"], "ts": g["ts"],
                    "falta": round(max(0.0, 100.0 - g["pct"]), 2),
                    "cands": [{"nome": c["nome"], "pct": c["pct"]} for c in g.get("cands", [])],
                })
            d["grupos"] = grupos
            if b.get("comparacao"):
                c = b["comparacao"]
                d["cmp"] = {
                    "lula_agora": c.get("lula_agora"),
                    "oponente_agora": c.get("oponente_agora"),
                    "pct_2022": c["ref"]["pct"], "lula_2022": c["ref"]["lula"],
                    "oponente_2022": c["ref"]["oponente"], "hora_2022": c["ref"]["hora"],
                }
        return d

    prop: dict[str, dict] = {}
    for chave, d in (proporcionais or {}).items():
        if not d or d.get("erro"):
            continue
        prop[chave] = {
            "rotulo": d.get("rotulo"),
            "pct_apurado": _f(d.get("pct_apurado")),
            "validos": d.get("validos"),
            "candidatos": d.get("candidatos"),
            "vagas": d.get("vagas"), "quociente": _f(d.get("quociente")),
            "piso": _f(d.get("piso")), "projetados": d.get("projetados"),
            "grupos": [{"grupo": g.get("grupo"), "cadeiras": g.get("cadeiras")}
                       for g in (d.get("grupos") or [])[:9]],
            "mais_votados": [{"nome": c.get("nome"), "partido": c.get("partido"),
                              "votos": c.get("votos"), "pct": _f(c.get("pct")),
                              "projetado": bool(c.get("projetado")),
                              "foto": _foto_mini(c.get("foto"))}
                             for c in d.get("mais_votados", [])[:8]],
            "eleitos_lista": [{"nome": x.get("nome"), "partido": x.get("partido"),
                               "votos": x.get("votos"), "pct": _f(x.get("pct"))}
                              for x in (d.get("eleitos_lista") or [])],
            "partidos": [{"partido": p.get("partido"), "pct": _f(p.get("pct"))}
                         for p in d.get("partidos", [])[:8]],
        }

    govs = governadores or []
    return {
        "gerado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "prop": prop,
        "blocos": ([um_bloco(pres, True)] if pres else [])
                  + [um_bloco(g, False) for g in governos],
        "eleitos": sorted([{"uf": g["uf"], "nome": g["nome"], "partido": g["partido"],
                            "pct": g["pct"], "cor": g["cor"],
                            "regiao": g.get("regiao", "—")}
                           for g in govs if g["vence_1t"]], key=lambda x: -x["pct"]),
    }


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import painel_eleicao as pe
    import mapa_governadores as mg

    blocos = pe.monta()
    govs = mg.busca_governadores()
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        Path.home() / ".hermes" / "eleicao" / "painel.html")
    destino.write_text(gera(blocos, governadores=govs), encoding="utf-8")
    (destino.parent / "dados.json").write_text(
        json.dumps(dados(blocos, govs), ensure_ascii=False), encoding="utf-8")
    print(f"  html: {destino} ({destino.stat().st_size} bytes)")
    print(f"  json: {(destino.parent / 'dados.json').stat().st_size} bytes")
