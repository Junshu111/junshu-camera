'use strict';
document.querySelectorAll('[data-ai-copy]').forEach(button=>button.addEventListener('click',async()=>{
 const block=button.closest('.ai-start');const target=document.getElementById(button.dataset.aiCopy);const value=target.innerText;const status=block.querySelector('.ai-copy-status');
 try{await navigator.clipboard.writeText(value);status.textContent='已复制。现在粘贴到你的 AI 对话框并发送。';}
 catch{const selection=window.getSelection();const range=document.createRange();range.selectNodeContents(target);selection.removeAllRanges();selection.addRange(range);status.textContent='已选中文字，请使用系统复制快捷键或长按复制，再粘贴到 AI 对话框。';}
}));
