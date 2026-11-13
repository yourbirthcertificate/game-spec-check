'use strict';
// The update notice on the main page and the update section on the About page.
// Release names and notes come from GitHub and are always escaped.

(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DISMISS_KEY = 'gsc-update-dismissed';

  async function call(path, opts) {
    let res;
    try {
      res = await fetch(path, opts);
    } catch {
      throw new Error('The app has stopped responding. Start it again and reload this page.');
    }
    const data = await res.json().catch(() => ({ error: 'The app sent back something unreadable.' }));
    if (!res.ok) throw new Error(data.error || 'That request failed.');
    return data;
  }
  const post = (path, body) => call(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  });

  const check = force => call('/api/update' + (force ? '?force=1' : ''));
  const setAuto = on => post('/api/settings', { update_check: on });

  function dismissed(version) {
    try { return localStorage.getItem(DISMISS_KEY) === version; } catch { return false; }
  }
  function dismiss(version) {
    try { localStorage.setItem(DISMISS_KEY, version); } catch { /* not saved, that's fine */ }
  }

  // What can be done about a newer version: a button, or a sentence saying why not.
  function actionHtml(info) {
    if (info.can_install) return '<button class="btn primary" type="button" data-up-install>Update now</button>';
    return `<span class="small">${esc(info.install_note || '')}</span>`;
  }

  function newerHtml(info, extra = '') {
    const notes = info.notes || info.name ? `<details class="up-notes"><summary>What changed</summary>
      <p><strong>${esc(info.name)}</strong></p><p class="up-text">${esc(info.notes || 'No notes for this release.')}</p></details>` : '';
    return `<div class="up-main"><span>Version ${esc(info.latest)} is available. You have ${esc(info.current)}.
        <a href="${esc(info.url)}" target="_blank" rel="noopener noreferrer">Release page</a></span>
      <span class="row-btns">${actionHtml(info)}${extra}</span></div>
      ${notes}<p class="error small" data-up-error role="alert" hidden></p>`;
  }

  async function waitForVersion(version, el) {
    const until = Date.now() + 120000;
    while (Date.now() < until) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const res = await fetch('/api/ping', { cache: 'no-store' });
        const ping = await res.json();
        if (ping.version === version) { location.reload(); return; }
      } catch { /* the old copy has stopped and the new one is starting */ }
    }
    el.textContent = 'The new version did not answer on this address. Start Game Spec Check again.';
  }

  async function install(box, btn) {
    const err = box.querySelector('[data-up-error]');
    if (err) err.hidden = true;
    btn.disabled = true;
    btn.textContent = 'Downloading…';
    try {
      const done = await post('/api/update/install', {});
      const msg = document.createElement('span');
      msg.textContent = `Version ${done.version} is installed. Restarting…`;
      btn.replaceWith(msg);
      waitForVersion(done.version, msg);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = 'Update now';
      if (err) { err.textContent = e.message; err.hidden = false; }
    }
  }

  // Main page: a notice when a newer version exists, unless the person dismissed that version.
  async function notice(el) {
    let info;
    try { info = await check(false); } catch { return; }
    if (!info.newer || dismissed(info.latest)) return;
    el.innerHTML = newerHtml(info, '<button class="link small" type="button" data-up-dismiss>Not now</button>');
    el.hidden = false;
    el.addEventListener('click', e => {
      if (e.target.closest('[data-up-dismiss]')) { dismiss(info.latest); el.hidden = true; }
      const b = e.target.closest('[data-up-install]');
      if (b) install(el, b);
    });
  }

  // About page: the setting, a "Check for updates" button and the result.
  async function section(root) {
    const auto = root.querySelector('#up-auto');
    const btn = root.querySelector('#up-check');
    const out = root.querySelector('#up-result');
    const show = (info, asked) => {
      root.querySelector('#up-current').textContent = info.current;
      auto.checked = info.enabled;
      auto.disabled = btn.disabled = !!info.off_for_session;
      if (info.off_for_session) {
        out.innerHTML = '<p class="small">Update checks are turned off for this session (<code>--no-update-check</code>).</p>';
      } else if (info.error) {
        out.innerHTML = `<p class="small error">${esc(info.error)}</p>`;
      } else if (info.newer) {
        out.innerHTML = newerHtml(info);
      } else if (info.checked) {
        out.innerHTML = `<p class="small">This is the newest version${asked ? '' : ' (checked in the last day)'}.</p>`;
      } else {
        out.innerHTML = '';
      }
    };
    try { show(await check(false), false); } catch (e) { out.innerHTML = `<p class="small error">${esc(e.message)}</p>`; }
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      out.innerHTML = '<p class="small">Checking…</p>';
      try { show(await check(true), true); } catch (e) { out.innerHTML = `<p class="small error">${esc(e.message)}</p>`; }
      btn.disabled = false;
    });
    auto.addEventListener('change', async () => {
      try { await setAuto(auto.checked); } catch (e) {
        auto.checked = !auto.checked;
        out.innerHTML = `<p class="small error">${esc(e.message)}</p>`;
      }
    });
    out.addEventListener('click', e => {
      const b = e.target.closest('[data-up-install]');
      if (b) install(out, b);
    });
  }

  window.GSCUpdate = { notice, section };
  if (document.getElementById('up-check')) section(document);
})();
