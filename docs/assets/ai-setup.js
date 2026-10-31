'use strict';
(() => {
  function selectText(area) {
    area.focus({ preventScroll: true });
    area.select();
    area.setSelectionRange(0, area.value.length);
  }

  function copyDuringClick(value) {
    // Run this synchronous fallback while the original user gesture is active.
    // It also works in browsers that do not expose navigator.clipboard.
    const previous = document.activeElement;
    const area = document.createElement('textarea');
    area.value = value;
    area.readOnly = true;
    area.setAttribute('aria-hidden', 'true');
    area.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none;font-size:16px';
    document.body.appendChild(area);
    let copied = false;
    try {
      selectText(area);
      copied = typeof document.execCommand === 'function' && document.execCommand('copy');
    } catch (_) { /* Continue with the Clipboard API or visible manual field. */ }
    area.remove();
    if (previous && typeof previous.focus === 'function') previous.focus({ preventScroll: true });
    return copied;
  }

  document.querySelectorAll('[data-ai-copy]').forEach(button => {
    const original = button.innerHTML;
    const action = button.closest('.ai-copy-action') || button.parentElement;
    let status = action.querySelector('.ai-button-status');
    if (!status) {
      status = document.createElement('p');
      status.className = 'ai-button-status';
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      button.insertAdjacentElement('afterend', status);
    }
    let reset;
    button.addEventListener('click', async () => {
      const target = document.getElementById(button.dataset.aiCopy);
      if (!target) {
        status.textContent = '暂时找不到复制内容，请刷新页面后重试。';
        return;
      }
      const value = typeof target.value === 'string' ? target.value : target.innerText;
      const container = button.closest('.ai-link-block, .ai-prompt-block') || action;
      clearTimeout(reset);
      button.textContent = '正在复制…';
      button.disabled = true;
      let copied = copyDuringClick(value);
      if (!copied && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        let deadline;
        try {
          await Promise.race([
            navigator.clipboard.writeText(value),
            new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Clipboard permission wait expired')), 1500); })
          ]);
          copied = true;
        } catch (_) { /* Show an honest, usable manual fallback below. */ }
        finally { clearTimeout(deadline); }
      }
      button.disabled = false;
      if (copied) {
        button.textContent = '已复制 ✓';
        status.textContent = '现在粘贴到同一个 AI 对话框并发送。';
        const manual = container.querySelector('.ai-manual-copy');
        if (manual) manual.hidden = true;
        reset = setTimeout(() => { button.innerHTML = original; }, 3000);
      } else {
        button.innerHTML = original;
        status.textContent = '浏览器未允许自动复制，请在下方手动复制。';
        let manual = container.querySelector('.ai-manual-copy');
        if (!manual) {
          manual = document.createElement('div');
          manual.className = 'ai-manual-copy';
          const label = document.createElement('label');
          label.textContent = '手动复制内容';
          const area = document.createElement('textarea');
          area.readOnly = true;
          area.rows = button.dataset.aiCopy === 'ai-repo' ? 2 : 7;
          area.addEventListener('click', () => selectText(area));
          label.appendChild(area);
          const help = document.createElement('p');
          help.textContent = '文字已选中。电脑按 ⌘C（Mac）或 Ctrl+C（Windows）；手机长按文字选择“复制”。';
          manual.append(label, help);
          container.appendChild(manual);
        }
        manual.hidden = false;
        const area = manual.querySelector('textarea');
        area.value = value;
        selectText(area);
      }
    });
  });
})();
