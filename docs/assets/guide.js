'use strict';
const search=document.querySelector('#chapter-search');
if(search){search.addEventListener('input',()=>{const q=search.value.trim().toLowerCase();let n=0;document.querySelectorAll('.chapter-row').forEach(row=>{row.hidden=!row.textContent.toLowerCase().includes(q);if(!row.hidden)n++;});document.querySelectorAll('.stage-block').forEach(stage=>{stage.hidden=![...stage.querySelectorAll('.chapter-row')].some(r=>!r.hidden);});document.querySelector('#search-count').textContent=q?'找到 '+n+' 章':'共 16 章，按顺序阅读';document.querySelector('#no-results').hidden=n!==0;});}
const boxes=[...document.querySelectorAll('[data-check]')];
let storageAvailable=true;
try{boxes.forEach(box=>{box.checked=localStorage.getItem('junshu-guide-r1:'+box.dataset.check)==='1';});}catch(e){storageAvailable=false;}
function progress(){const label=document.querySelector('.checks-status');if(label)label.textContent='本章已勾选 '+boxes.filter(x=>x.checked).length+' / '+boxes.length+' 项'+(storageAvailable?' · 记录仅保存在当前浏览器':' · 当前浏览器未开放保存权限');}
boxes.forEach(box=>box.addEventListener('change',()=>{try{localStorage.setItem('junshu-guide-r1:'+box.dataset.check,box.checked?'1':'0');}catch(e){storageAvailable=false;}progress();}));progress();
const copyButton=document.querySelector('#copy-prompt');
if(copyButton)copyButton.addEventListener('click',async()=>{const area=document.querySelector('#ai-prompt');const status=document.querySelector('#copy-status');try{await navigator.clipboard.writeText(area.value);status.textContent='已复制，可以粘贴到 Codex 等 AI 工具中。';}catch(e){area.focus();area.select();status.textContent='已选中文字，请使用系统复制快捷键或长按复制。';}});
const selectButton=document.querySelector('#select-prompt');
if(selectButton)selectButton.addEventListener('click',()=>{const area=document.querySelector('#ai-prompt');area.focus();area.select();document.querySelector('#copy-status').textContent='已选中文字，可手动复制。';});
