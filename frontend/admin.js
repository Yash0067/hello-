/* ============================================================
   Adarsh Boutique - Tailor Shop Management System (admin SPA)
   Vanilla JS. Talks to the Python backend with a Bearer token.
   Every response 401 -> back to login.
   ============================================================ */
(function () {
'use strict';

var API = (window.APP_CONFIG && window.APP_CONFIG.apiBase) || '';
var $ = function (sel, el) { return (el || document).querySelector(sel); };
var view = $('#view');
var authenticated = false;

/* ---------------- tiny helpers ---------------- */
function esc(s) {  // escape user data before innerHTML - XSS protection
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function toast(msg, isErr) {
  var t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(function () { t.className = 'toast'; }, 3600);
}
function fmtDate(iso) {
  if (!iso) return '—';
  var d = new Date(String(iso).replace(' ', 'T'));
  if (isNaN(d)) return String(iso);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) +
    ', ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}
function todayISO() { return new Date().toISOString().slice(0, 10); }
function initials(name) {
  return String(name || '?').trim().split(/\s+/).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
}
function debounce(fn, ms) {
  var t; return function () { var a = arguments, self = this;
    clearTimeout(t); t = setTimeout(function () { fn.apply(self, a); }, ms); };
}

/* ---------------- auth ---------------- */
function logout(localOnly) {
  if (!localOnly) api('POST', '/api/logout').catch(function () {});
  authenticated = false;
  showGate();   // stay on THIS page - some preview windows lose everything on navigation
}
$('#logout-btn').addEventListener('click', function () { logout(false); });

/* ---------------- in-page sign-in gate ----------------
   The gate is used after a session expires or logout. */
function showGate() {
  if ($('#gate')) { $('#gate').style.display = 'flex'; return; }
  var gate = document.createElement('div');
  gate.id = 'gate';
  gate.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(40,20,26,.6)';
  gate.innerHTML =
    '<form id="gate-form" style="background:#fffaf3;border-radius:16px;padding:30px 32px;width:330px;box-shadow:0 18px 50px rgba(0,0,0,.35)">' +
    '<h2 style="margin:0 0 4px;color:#5c1a2e;font-size:21px">Adarsh Boutique</h2>' +
    '<p style="margin:0 0 16px;color:#8a6f5f;font-size:13px">Sign in to the shop dashboard</p>' +
    '<input id="gate-user" placeholder="Username" autocomplete="username" style="width:100%;padding:11px 12px;margin-bottom:10px;border:1px solid #d9c3b8;border-radius:8px;font-size:14px;box-sizing:border-box">' +
    '<input id="gate-pass" type="password" placeholder="Password" autocomplete="current-password" style="width:100%;padding:11px 12px;margin-bottom:12px;border:1px solid #d9c3b8;border-radius:8px;font-size:14px;box-sizing:border-box">' +
    '<button id="gate-btn" type="submit" style="width:100%;padding:12px;border:0;border-radius:8px;background:#5c1a2e;color:#fff;font-size:14px;font-weight:600;cursor:pointer">Sign in &rarr;</button>' +
    '<p id="gate-status" style="margin:10px 0 0;color:#a33;font-size:13px;min-height:16px"></p>' +
    '</form>';
  document.body.appendChild(gate);
  $('#gate-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var u = $('#gate-user').value.trim(), p = $('#gate-pass').value;
    var btn = $('#gate-btn'), status = $('#gate-status');
    if (!u || !p) { status.textContent = 'Please enter your username and password.'; return; }
    btn.disabled = true; btn.textContent = 'Signing in…'; status.textContent = '';
    fetch(API + '/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; });
      })
      .then(function (res) {
        if (res.ok && res.d && res.d.ok) {
          gate.remove();
          authenticated = true;
          try { $('#who').textContent = res.d.username || u; } catch (_) {}
          route();          // open the dashboard - same page, zero navigation
        } else {
          btn.disabled = false; btn.textContent = 'Sign in \u2192';
          status.textContent = (res.d && res.d.error) ||
            (res.status === 429 ? 'Too many attempts. Please wait about 15 minutes.'
                                : 'Sign in failed. Please try again.');
          $('#gate-pass').value = ''; $('#gate-pass').focus();
        }
      })
      .catch(function () {
        btn.disabled = false; btn.textContent = 'Sign in \u2192';
        status.textContent = 'Cannot reach the server. Please try again.';
      });
  });
  try { $('#gate-user').focus(); } catch (_) {}
}

function api(method, path, body) {
  var headers = {
    'Content-Type': 'application/json',
    'X-Requested-With': 'fetch'
  };
  return fetch(API + path, {
    method: method,
    headers: headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body)
  }).then(function (r) {
    if (r.status === 401) { logout(true); throw new Error('Please sign in again.'); }
    return r.json().catch(function () { return {}; }).then(function (data) {
      if (!r.ok) {
        var err = new Error((data && data.error) || ('Request failed (' + r.status + ')'));
        err.data = data; err.status = r.status;
        throw err;
      }
      return data;
    });
  });
}

/* ---------------- theme ---------------- */
function applyTheme() {
  var saved = 'light';
  try { saved = localStorage.getItem('ab_theme') || 'light'; } catch (_) { saved = 'light'; }
  document.documentElement.setAttribute('data-theme', saved);
}
$('#theme-btn').addEventListener('click', function () {
  var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('ab_theme', next); } catch (_) {}
  applyTheme();
});
applyTheme();

/* ---------------- mobile sidebar ---------------- */
$('#menu-btn').addEventListener('click', function () { $('#sidebar').classList.toggle('open'); });
document.addEventListener('click', function (e) {
  var sb = $('#sidebar');
  if (sb.classList.contains('open') && !sb.contains(e.target) && e.target.closest('#menu-btn') === null &&
      !e.target.closest('#sidebar')) sb.classList.remove('open');
});

/* ---------------- router ---------------- */
var ROUTES = {
  '#/dashboard':     { title: 'Dashboard',        render: renderDashboard },
  '#/customers':     { title: 'Customers',        render: renderCustomers },
  '#/measurements':  { title: 'Measurements',     render: renderMeasurementsIndex },
  '#/orders':        { title: 'Orders',           render: renderOrders },
  '#/appointments':  { title: 'Appointments',     render: renderAppointments },
  '#/settings':      { title: 'Settings',         render: renderSettings }
};
var currentClient = null;   // cached customer record for the client page
var lastClientQuery = '';

function route() {
  var hash = location.hash || '#/dashboard';
  $('#sidebar').classList.remove('open');
  document.querySelectorAll('#nav button[data-route]').forEach(function (b) {
    var on = hash.indexOf(b.getAttribute('data-route')) === 0;
    b.classList.toggle('active', on);
  });
  var clientMatch = hash.match(/^#\/client\/([A-Za-z0-9-]+)/);
  if (clientMatch) {
    $('#page-title').textContent = 'Customer profile';
    renderClientPage(clientMatch[1]);
    return;
  }
  var def = ROUTES[hash] || ROUTES['#/dashboard'];
  $('#page-title').textContent = def.title;
  def.render().catch(function (err) { view.innerHTML = '<div class="empty">' + esc(err.message) + '</div>'; });
}
window.addEventListener('hashchange', route);
document.querySelectorAll('#nav button[data-route]').forEach(function (b) {
  b.addEventListener('click', function () { location.hash = b.getAttribute('data-route'); });
});
$('#nav-bill').addEventListener('click', function () { openBill(); });
$('#refresh-btn').addEventListener('click', route);

/* ============================================================
   DASHBOARD
   ============================================================ */
function renderDashboard() {
  view.innerHTML = '<div class="empty">Loading dashboard…</div>';
  return api('GET', '/api/stats').then(function (s) {
    var today = todayISO();
    view.innerHTML =
      '<div class="cards">' +
        statCard('Customers', s.total_customers, 'people in your book') +
        statCard('Measurements saved', s.total_measurements, 'measurement sessions') +
        statCard("Today's appointments", s.todays_appointments, fmtDate(today).split(',')[0]) +
        statCard('Pending orders', s.pending_orders, s.pending_orders > 0 ? 'to stitch & deliver' : 'all caught up') +
      '</div>' +
      '<div class="grid-2">' +
        '<div class="panel"><div class="panel-h"><h3>Recently added customers</h3>' +
          '<button class="btn ghost small" data-go="#/customers">View all →</button></div>' +
          '<div id="recent-wrap"></div></div>' +
        '<div class="panel"><div class="panel-h"><h3>Quick actions</h3></div><div class="panel-b" id="quick"></div></div>' +
      '</div>';
    var rows = (s.recent_customers || []).map(function (c) {
      return '<tr class="rowlink" data-cid="' + esc(c.client_id) + '"><td><b>' + esc(c.name) + '</b>' +
        (c.current_measurement ? '<span class="sub">Last: ' + esc(c.current_measurement.garment_type) + ' · ' + esc(fmtDate(c.current_measurement.updated_at)) + '</span>' : '<span class="sub">No measurements yet</span>') +
        '</td><td style="text-align:right;color:var(--muted)">' + esc(c.phone) + '</td></tr>';
    }).join('');
    $('#recent-wrap').innerHTML = rows
      ? '<table class="table"><tbody>' + rows + '</tbody></table>'
      : '<div class="empty">No customers yet. Add your first one!</div>';
    $('#recent-wrap').addEventListener('click', function (e) {
      var tr = e.target.closest('tr[data-cid]');
      if (tr) location.hash = '#/client/' + tr.getAttribute('data-cid');
    });
    $('#quick').innerHTML =
      '<div style="display:grid;gap:9px">' +
      '<button class="btn" id="qa-customer">＋ Add new customer</button>' +
      '<button class="btn ghost" id="qa-order">🧵 Create an order</button>' +
      '<button class="btn ghost" id="qa-bill">🧾 Create a bill</button>' +
      '<button class="btn ghost" id="qa-appt">📅 View appointments</button>' +
      '<a class="btn ghost" href="/" target="_blank" rel="noopener">🌐 Open public website</a></div>';
    $('#qa-customer').addEventListener('click', function () { location.hash = '#/customers'; setTimeout(openAddCustomer, 250); });
    $('#qa-order').addEventListener('click', function () { location.hash = '#/orders'; setTimeout(openAddOrder, 250); });
    $('#qa-bill').addEventListener('click', function () { openBill(); });
    $('#qa-appt').addEventListener('click', function () { location.hash = '#/appointments'; });
  });
}
function openBill(clientId) {
  return api('GET', '/api/clients').then(function (data) {
    var clients = data.clients || [];
    if (!clients.length) { toast('Add a customer before creating a bill.', true); return; }
    var options = clients.map(function (c) {
      return '<option value="' + esc(c.client_id) + '"' + (c.client_id === clientId ? ' selected' : '') + '>' +
        esc(c.name) + ' · ' + esc(c.phone) + '</option>';
    }).join('');
    var back = modal('Create bill',
      '<div class="field"><label>Customer *</label><select id="bf-client">' + options + '</select></div>' +
      '<div class="form-row"><div class="field"><label>Item / service *</label><input id="bf-item" maxlength="80" placeholder="e.g. Blouse stitching"></div>' +
      '<div class="field"><label>Amount (₹) *</label><input id="bf-amount" type="number" min="0" step="0.01" placeholder="1200"></div></div>' +
      '<div class="form-row"><div class="field"><label>Discount (₹)</label><input id="bf-discount" type="number" min="0" step="0.01" value="0"></div>' +
      '<div class="field"><label>Advance paid (₹)</label><input id="bf-advance" type="number" min="0" step="0.01" value="0"></div></div>' +
      '<p id="bf-total" class="sub" style="margin:8px 0 0"></p>',
      '<button class="btn ghost" data-close>Cancel</button><button class="btn" id="bf-send">🧾 Open WhatsApp bill</button>');
    function updateTotal() {
      var amount = Math.max(0, Number($('#bf-amount').value) || 0);
      var discount = Math.max(0, Number($('#bf-discount').value) || 0);
      var advance = Math.max(0, Number($('#bf-advance').value) || 0);
      var total = Math.max(0, amount - discount);
      $('#bf-total').textContent = 'Total: ₹' + total.toFixed(2) + ' · Balance: ₹' + Math.max(0, total - advance).toFixed(2);
    }
    ['bf-amount', 'bf-discount', 'bf-advance'].forEach(function (id) { $('#' + id).addEventListener('input', updateTotal); });
    updateTotal();
    $('#bf-send').addEventListener('click', function () {
      var customer = clients.find(function (c) { return c.client_id === $('#bf-client').value; });
      var item = $('#bf-item').value.trim();
      var amount = Math.max(0, Number($('#bf-amount').value) || 0);
      var discount = Math.max(0, Number($('#bf-discount').value) || 0);
      var advance = Math.max(0, Number($('#bf-advance').value) || 0);
      if (!item || amount <= 0) { toast('Enter an item and amount.', true); return; }
      var total = Math.max(0, amount - discount);
      var balance = Math.max(0, total - advance);
      var message = '*ADARSH BOUTIQUE*\n' +
        '━━━━━━━━━━━━━━━━━━\n' +
        '*Bill for:* ' + customer.name + '\n' +
        '*Mobile:* ' + customer.phone + '\n\n' +
        '*ITEM DETAILS*\n' +
        '• ' + item + ' — ₹' + amount.toFixed(2) +
        (discount ? '\n• Discount — ₹' + discount.toFixed(2) : '') +
        '\n\n*PAYMENT SUMMARY*\n' +
        '*Total:* ₹' + total.toFixed(2) + '\n' +
        '*Advance paid:* ₹' + advance.toFixed(2) + '\n' +
        '*Balance due:* ₹' + balance.toFixed(2) +
        '\n\nThank you for choosing *Adarsh Boutique*!\nWe look forward to serving you again.';
      window.open('https://wa.me/' + customer.phone.replace(/\D/g, '') + '?text=' + encodeURIComponent(message), '_blank', 'noopener');
      closeModal();
    });
  });
}
function statCard(k, v, s) {
  return '<div class="card stat"><div class="k">' + esc(k) + '</div><div class="v">' + esc(v) +
         '</div><div class="s">' + esc(s) + '</div></div>';
}

/* ============================================================
   CUSTOMERS
   ============================================================ */
function renderCustomers() {
  view.innerHTML =
    '<div class="panel"><div class="panel-h">' +
      '<input id="cust-search" placeholder="Search by name or mobile…" value="' + esc(lastClientQuery) + '" ' +
      'style="flex:1;min-width:0;background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:10px 13px;outline:none" aria-label="Search customers">' +
      '<button class="btn" id="add-cust">＋ Add new customer</button></div>' +
      '<div id="cust-list"><div class="empty">Loading…</div></div></div>';
  $('#add-cust').addEventListener('click', openAddCustomer);
  var doSearch = debounce(function () {
    lastClientQuery = $('#cust-search').value;
    loadCustomers(lastClientQuery);
  }, 250);
  $('#cust-search').addEventListener('input', doSearch);
  return loadCustomers(lastClientQuery);
}
function loadCustomers(query) {
  var wrap = $('#cust-list');
  if (!wrap) return Promise.resolve();
  return api('GET', '/api/clients' + (query ? '?q=' + encodeURIComponent(query) : '')).then(function (data) {
    var clients = data.clients || [];
    if (!clients.length) {
      wrap.innerHTML = '<div class="empty">' + (query ? 'No customers match “' + esc(query) + '”.' :
        'No customers yet — add your first one with the button above.') + '</div>';
      return;
    }
    var rows = clients.map(function (c) {
      var m = c.current_measurement;
      return '<tr class="rowlink" data-cid="' + esc(c.client_id) + '">' +
        '<td><b>' + esc(c.name) + '</b>' + (c.email ? '<span class="sub">' + esc(c.email) + '</span>' : '') + '</td>' +
        '<td>' + esc(c.phone) + '</td>' +
        '<td>' + (m ? '<span class="badge info">' + esc(m.garment_type) + '</span>' : '<span class="badge">—</span>') + '</td>' +
        '<td style="color:var(--muted);font-size:12px">' + esc(fmtDate(m ? m.updated_at : c.created_at)) + '</td>' +
        '<td style="text-align:right;white-space:nowrap">' +
        '<button class="btn ghost small" data-bill="' + esc(c.client_id) + '">🧾 Bill</button> ' +
        '<button class="btn ghost small" data-edit="' + esc(c.client_id) + '">Edit</button> ' +
        '<button class="btn danger small" data-del="' + esc(c.client_id) + '" data-name="' + esc(c.name) + '">Delete</button></td></tr>';
    }).join('');
    wrap.innerHTML = '<table class="table"><thead><tr><th>Customer</th><th>Mobile</th><th>Last measurement</th><th>Updated</th><th>Actions</th></tr></thead><tbody>' + rows + '</tbody></table>';
    wrap.onclick = function (e) {
      var bill = e.target.closest('[data-bill]'), edit = e.target.closest('[data-edit]'),
        del = e.target.closest('[data-del]'), row = e.target.closest('tr[data-cid]');
      if (bill) { e.stopPropagation(); openBill(bill.getAttribute('data-bill')); return; }
      if (edit) { e.stopPropagation(); openEditCustomer(edit.getAttribute('data-edit')); return; }
      if (del) { e.stopPropagation(); confirmDelete(del.getAttribute('data-del'), del.getAttribute('data-name')); return; }
      if (row) location.hash = '#/client/' + row.getAttribute('data-cid');
    };
  }).catch(function (err) { wrap.innerHTML = '<div class="empty">' + esc(err.message) + '</div>'; });
}

/* -------- add / edit / delete modals -------- */
function closeModal() { var m = $('.modal-back'); if (m) m.remove(); }
function modal(title, bodyHTML, footerHTML) {
  closeModal();
  var back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = '<div class="modal" role="dialog" aria-modal="true" aria-label="' + esc(title) + '">' +
    '<div class="modal-h"><h3>' + esc(title) + '</h3><button class="icon-btn" data-close aria-label="Close">✕</button></div>' +
    '<div class="modal-b">' + bodyHTML + '</div><div class="modal-f">' + footerHTML + '</div></div>';
  document.body.appendChild(back);
  back.addEventListener('click', function (e) { if (e.target === back || e.target.closest('[data-close]')) closeModal(); });
  document.addEventListener('keydown', escListener);
  return back;
}
function escListener(e) { if (e.key === 'Escape') { closeModal(); document.removeEventListener('keydown', escListener); } }

function customerForm(c) {
  c = c || {};
  return '<div class="field"><label>Full name *</label><input id="cf-name" maxlength="80" value="' + esc(c.name || '') + '"><div class="err" id="cf-name-err">Please enter the customer name.</div></div>' +
    '<div class="form-row"><div class="field"><label>Mobile number *</label><input id="cf-phone" maxlength="18" inputmode="tel" value="' + esc(c.phone || '') + '" placeholder="e.g. 98765 43210"><div class="err" id="cf-phone-err">Enter a valid mobile number.</div></div>' +
    '<div class="field"><label>Email (optional)</label><input id="cf-email" maxlength="120" value="' + esc(c.email || '') + '"></div></div>' +
    '<div class="field"><label>Address (optional)</label><textarea id="cf-address" maxlength="300">' + esc(c.address || '') + '</textarea></div>';
}
function readCustomerForm() {
  return {
    name: $('#cf-name').value.trim(),
    phone: $('#cf-phone').value.trim(),
    email: $('#cf-email').value.trim(),
    address: $('#cf-address').value.trim()
  };
}
function validateCustomerUI(data) {
  var ok = true;
  $('#cf-name').closest('.field').classList.toggle('invalid', !data.name); if (!data.name) ok = false;
  var digits = data.phone.replace(/\D/g, '');
  var phoneOk = digits.length >= 7 && digits.length <= 15;
  $('#cf-phone').closest('.field').classList.toggle('invalid', !phoneOk); if (!phoneOk) ok = false;
  return ok;
}
function openAddCustomer() {
  var back = modal('Add new customer', customerForm(),
    '<button class="btn ghost" data-close>Cancel</button><button class="btn" id="cf-save">Save customer</button>');
  $('#cf-save', back).addEventListener('click', function () {
    var data = readCustomerForm();
    if (!validateCustomerUI(data)) return;
    $('#cf-save').disabled = true;
    api('POST', '/api/clients', data).then(function (rec) {
      closeModal(); toast('Customer "' + rec.name + '" added ✓');
      if (location.hash.indexOf('#/client/') === 0 || location.hash === '#/customers') route();
      else location.hash = '#/client/' + rec.client_id;
    }).catch(function (err) {
      $('#cf-save').disabled = false;
      if (err.status === 409 && err.data && err.data.existing) {
        if (confirm('A customer with this number already exists:\n\n' + err.data.existing.name + ' · ' + err.data.existing.phone +
          '\n\nOpen that profile instead?')) {
          closeModal(); location.hash = '#/client/' + err.data.existing.client_id;
        }
      } else toast(err.message, true);
    });
  });
  $('#cf-name', back).focus();
}
function openEditCustomer(cid) {
  api('GET', '/api/clients/' + cid).then(function (c) {
    var back = modal('Edit customer', customerForm(c),
      '<button class="btn ghost" data-close>Cancel</button><button class="btn" id="cf-save">Save changes</button>');
    $('#cf-save', back).addEventListener('click', function () {
      var data = readCustomerForm();
      if (!validateCustomerUI(data)) return;
      $('#cf-save').disabled = true;
      api('PUT', '/api/clients/' + cid, data).then(function () {
        closeModal(); toast('Customer updated ✓'); route();
      }).catch(function (err) { $('#cf-save').disabled = false; toast(err.message, true); });
    });
  }).catch(function (err) { toast(err.message, true); });
}
function confirmDelete(cid, name) {
  var back = modal('Delete customer?',
    '<p style="margin:0 0 8px">This permanently removes <b>' + esc(name) + '</b> along with all measurement history and orders.</p>' +
    '<p style="margin:0;color:var(--bad);font-size:12.5px">This cannot be undone. Google Sheets rows are removed too.</p>',
    '<button class="btn ghost" data-close>Keep customer</button><button class="btn danger" id="del-go">Delete permanently</button>');
  $('#del-go', back).addEventListener('click', function () {
    api('DELETE', '/api/clients/' + cid).then(function () {
      closeModal(); toast('Customer deleted');
      if (location.hash.indexOf('#/client/') !== -1) location.hash = '#/customers'; else route();
    }).catch(function (err) { toast(err.message, true); });
  });
}

/* ============================================================
   MEASUREMENTS index (recent sessions across customers)
   ============================================================ */
function renderMeasurementsIndex() {
  view.innerHTML = '<div class="empty">Loading measurement sessions…</div>';
  return api('GET', '/api/clients').then(function (data) {
    var withM = (data.clients || []).filter(function (c) { return c.current_measurement && c.current_measurement.updated_at; });
    if (!withM.length) {
      view.innerHTML = '<div class="panel"><div class="empty">No measurements saved yet.<br><br>' +
        '<button class="btn" onclick="location.hash=\'#/customers\'">Open customers to start measuring</button></div></div>';
      return;
    }
    var rows = withM.map(function (c) {
      var m = c.current_measurement;
      var keys = Object.keys(m.values || {}).slice(0, 4).map(function (k) { return k + ' ' + m.values[k] + '″'; }).join(' · ');
      return '<tr class="rowlink" data-cid="' + esc(c.client_id) + '"><td><b>' + esc(c.name) + '</b><span class="sub">' + esc(c.phone) + '</span></td>' +
        '<td><span class="badge info">' + esc(m.garment_type) + '</span></td>' +
        '<td style="color:var(--muted);font-size:12.5px">' + esc(keys) + (Object.keys(m.values || {}).length > 4 ? ' …' : '') + '</td>' +
        '<td style="color:var(--muted);font-size:12px;white-space:nowrap">' + esc(fmtDate(m.updated_at)) + '</td></tr>';
    }).join('');
    view.innerHTML = '<div class="panel"><div class="panel-h"><h3>Latest measurement per customer</h3>' +
      '<span class="updated">Full history lives in each profile — nothing is ever overwritten</span></div>' +
      '<table class="table"><thead><tr><th>Customer</th><th>Garment</th><th>Values</th><th>Saved</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
    view.querySelector('tbody').addEventListener('click', function (e) {
      var tr = e.target.closest('tr[data-cid]');
      if (tr) location.hash = '#/client/' + tr.getAttribute('data-cid');
    });
  });
}

/* ============================================================
   CLIENT PAGE + VISUAL MEASUREMENT DESIGNER  (the highlight)
   ============================================================ */

/* Field dictionary: label, body group, sensible range in inches. */
var FIELDS = {
  neck:        { label: 'Neck',           min: 10, max: 25 },
  shoulder:    { label: 'Shoulder',       min: 11, max: 22 },
  chest:       { label: 'Chest / Bust',   min: 24, max: 60 },
  underarm:    { label: 'Armhole',        min: 11, max: 24 },
  bicep:       { label: 'Bicep',          min: 7,  max: 22 },
  sleeve:      { label: 'Sleeve length',  min: 3,  max: 26 },
  wrist:       { label: 'Wrist',          min: 4,  max: 12 },
  waist:       { label: 'Waist',          min: 20, max: 55 },
  hip:         { label: 'Hip',            min: 26, max: 64 },
  shirtlength: { label: 'Shirt length',   min: 18, max: 60 },
  thigh:       { label: 'Thigh',          min: 14, max: 36 },
  knee:        { label: 'Knee',           min: 10, max: 26 },
  calf:        { label: 'Calf',           min: 8,  max: 24 },
  ankle:       { label: 'Ankle',          min: 6,  max: 16 },
  inseam:      { label: 'Inseam',         min: 20, max: 38 },
  outseam:     { label: 'Outseam / pant length', min: 28, max: 50 }
};
var GARMENT_FIELDS = {
  Blouse: ['shoulder', 'chest', 'waist', 'neck', 'sleeve', 'underarm', 'bicep', 'wrist', 'shirtlength'],
  Shirt:  ['shoulder', 'chest', 'waist', 'neck', 'sleeve', 'underarm', 'bicep', 'wrist', 'shirtlength'],
  Pant:   ['waist', 'hip', 'thigh', 'knee', 'calf', 'ankle', 'inseam', 'outseam'],
  Kurta:  ['shoulder', 'chest', 'waist', 'hip', 'neck', 'sleeve', 'underarm', 'bicep', 'shirtlength'],
  Suit:   ['shoulder', 'chest', 'waist', 'hip', 'neck', 'sleeve', 'underarm', 'bicep', 'wrist', 'shirtlength', 'thigh', 'knee', 'calf', 'ankle', 'inseam', 'outseam'],
  Blazer: ['shoulder', 'chest', 'waist', 'neck', 'sleeve', 'underarm', 'bicep', 'wrist', 'shirtlength'],
  Custom: Object.keys(FIELDS)
};
var GARMENTS = Object.keys(GARMENT_FIELDS);
var IN_TO_CM = 2.54;

/* Markers on the SVG figure: key -> [x, y, label side]  (viewBox 240x560) */
var MARKERS = {
  neck:        [120, 74,  'right'],
  shoulder:    [88,  97,  'left'],
  underarm:    [91, 121,  'left'],
  chest:       [120, 140, 'right'],
  bicep:       [76, 158,  'left'],
  sleeve:      [73, 205,  'left'],
  wrist:       [68, 262,  'left'],
  waist:       [120, 185, 'right'],
  hip:         [120, 224, 'right'],
  shirtlength: [120, 300, 'right'],
  outseam:     [97, 300,  'left'],
  inseam:      [120, 330, 'right'],
  thigh:       [103, 330, 'left'],
  knee:        [106, 415, 'left'],
  calf:        [104, 470, 'left'],
  ankle:       [102, 522, 'left']
};

function bodySVG() {
  /* Clean front-view figure: head, neck, torso, arms, legs. */
  return '<svg class="body-svg" viewBox="0 0 240 560" role="img" aria-label="Body figure with measurement points">' +
    '<g class="figure-group">' +
    '<ellipse class="figure" cx="120" cy="38" rx="21" ry="26"/>' +                     /* head */
    '<path class="figure" d="M112 60 h16 v18 h-16 z"/>' +                              /* neck */
    '<path class="figure" d="M88 96 C98 82 142 82 152 96 L158 150 C158 172 150 185 148 200 L146 236 C146 252 134 258 120 258 C106 258 94 252 94 236 L92 200 C90 185 82 172 82 150 Z"/>' + /* torso */
    '<path class="figure" d="M96 258 C102 276 104 296 102 318 L108 318 C110 300 112 284 120 276 C128 284 130 300 132 318 L138 318 C136 296 138 276 144 258 C138 252 126 250 120 250 C114 250 102 252 96 258 Z" opacity=".92"/>' + /* hips+legs top */
    '<path class="figure" d="M102 318 L108 318 L112 430 L110 508 L104 508 L99 430 Z"/>' +  /* left leg */
    '<path class="figure" d="M132 318 L138 318 L141 430 L136 508 L130 508 L128 430 Z"/>' + /* right leg */
    '<path class="figure" d="M88 96 C80 100 74 118 72 150 C70 180 68 220 68 252 L74 254 C78 224 80 190 84 162 C86 150 88 130 90 118 Z"/>' +  /* left arm */
    '<path class="figure" d="M152 96 C160 100 166 118 168 150 C170 180 172 220 172 252 L166 254 C162 224 160 190 156 162 C154 150 152 130 150 118 Z"/>' + /* right arm */
    '<path class="figure-line" d="M88 96 C98 82 142 82 152 96"/>' +
    '<path class="figure-line" d="M99 430 L112 430 M128 430 L141 430" opacity=".4"/>' +
    '</g>' +
    Object.keys(MARKERS).map(function (key) {
      var m = MARKERS[key], side = m[2];
      var lx = side === 'left' ? m[0] - 14 : m[0] + 14;
      var anchor = side === 'left' ? 'end' : 'start';
      return '<g class="marker" data-mk="' + key + '" tabindex="0" role="button" aria-label="' + esc(FIELDS[key].label) + '">' +
        '<circle class="hit" cx="' + m[0] + '" cy="' + m[1] + '" r="16"/>' +
        '<circle class="dot" cx="' + m[0] + '" cy="' + m[1] + '" r="4.2"/>' +
        '<text class="lbl" x="' + lx + '" y="' + (m[1] + 3) + '" text-anchor="' + anchor + '">' + esc(FIELDS[key].label) + '</text></g>';
    }).join('') +
    '</svg>';
}

function renderClientPage(cid) {
  view.innerHTML = '<div class="empty">Loading customer…</div>';
  Promise.all([
    api('GET', '/api/clients/' + encodeURIComponent(cid)),
    api('GET', '/api/clients/' + encodeURIComponent(cid) + '/measurements')
  ]).then(function (res) {
    currentClient = res[0];
    var data = res[1] || { current: null, history: [] };
    drawClientPage(data);
  }).catch(function (err) {
    view.innerHTML = '<div class="panel"><div class="empty">' + esc(err.message) +
      '<br><br><button class="btn ghost" onclick="location.hash=\'#/customers\'">← Back to customers</button></div></div>';
  });
}

var mState = null;  // measurement designer state

function drawClientPage(data) {
  var c = currentClient;
  mState = {
    garment: (data.current && data.current.garment_type) || 'Blouse',
    unit: (data.current && data.current.unit_used) || 'in',
    values: Object.assign({}, data.current && data.current.values),
    custom: [],   // [{name, value}]
    notes: (data.current && data.current.notes) || '',
    history: data.history || [],
    compareA: null, // history session ids chosen for compare (null = current form)
    compareB: null
  };
  /* seed custom fields not in FIELDS from saved values */
  if (data.current && data.current.values) {
    Object.keys(data.current.values).forEach(function (k) {
      if (!FIELDS[k]) mState.custom.push({ name: k, value: data.current.values[k] });
    });
  }
  view.innerHTML =
    '<div class="client-top">' +
      '<span class="avatar">' + esc(initials(c.name)) + '</span>' +
      '<div style="min-width:0"><h3>' + esc(c.name) + '</h3><div class="phone">📱 ' + esc(c.phone) +
      (c.email ? ' &nbsp;✉ ' + esc(c.email) : '') +
      (c.address ? '<br>📍 ' + esc(c.address) : '') +
      '<span class="sub" style="display:block;margin-top:3px">Customer ID ' + esc(c.client_id) + ' · registered ' + esc(fmtDate(c.created_at)) + '</span></div></div>' +
      '<div class="actions">' +
        '<button class="btn ghost small" id="ct-edit">✏ Edit</button>' +
        '<button class="btn ghost small" id="ct-order">🧵 New order</button>' +
        '<button class="btn ghost small" id="ct-bill">🧾 Bill</button>' +
        '<button class="btn danger small" id="ct-del">Delete</button></div>' +
    '</div>' +

    '<div class="designer" id="designer">' +
      '<div class="body-panel" id="body-panel">' + bodySVG() + '</div>' +
      '<div class="measure-panel">' +
        '<div class="garment-tabs" id="garment-tabs" role="tablist" aria-label="Garment type">' +
          GARMENTS.map(function (g) {
            return '<button role="tab" data-g="' + g + '" class="' + (g === mState.garment ? 'active' : '') + '" aria-selected="' + (g === mState.garment) + '">' + g + '</button>';
          }).join('') + '</div>' +
        '<div class="measure-head">' +
          '<span class="updated" id="m-updated"></span>' +
          '<span class="unit-toggle" id="unit-toggle" role="group" aria-label="Units">' +
            '<button data-u="in" class="' + (mState.unit === 'in' ? 'active' : '') + '">inches</button>' +
            '<button data-u="cm" class="' + (mState.unit === 'cm' ? 'active' : '') + '">cm</button></span></div>' +
        '<div class="m-grid" id="m-grid"></div>' +
        '<div class="custom-fields" id="custom-fields"></div>' +
        '<div class="notes-box"><label>Notes (fit preferences, design details…)</label>' +
          '<textarea id="m-notes" maxlength="500" placeholder="e.g. boat neck, sleeves 2″ extra, slight A-line…">' + esc(mState.notes) + '</textarea></div>' +
        '<div class="measure-foot">' +
          '<span class="hint">Click a point on the figure to jump to its field. Saving keeps every old session.</span>' +
          '<button class="btn" id="m-save">💾 Save measurements</button></div>' +
      '</div>' +
    '</div>' +

        '</div>' +

    '<div class="panel" style="margin-top:14px" id="history-panel"></div>' +
    '<div class="panel" style="margin-top:14px" id="client-orders"></div>';

  $('#ct-edit').addEventListener('click', function () { openEditCustomer(c.client_id); });
  $('#ct-del').addEventListener('click', function () { confirmDelete(c.client_id, c.name); });
  $('#ct-order').addEventListener('click', function () { openAddOrder(c.client_id, c.name); });
  $('#ct-bill').addEventListener('click', function () { openBill(c.client_id); });
  bindDesigner();
  renderHistoryPanel();
  loadClientOrders();
}

/* ---------- designer behaviour ---------- */
function garmentFields() { return GARMENT_FIELDS[mState.garment] || GARMENT_FIELDS.Custom; }

function toDisplay(vIn) { return mState.unit === 'cm' ? +(vIn * IN_TO_CM).toFixed(1) : +Number(vIn).toFixed(2); }
function toInches(v) { return mState.unit === 'cm' ? v / IN_TO_CM : v; }
function unitLabel() { return mState.unit; }

function renderMeasureGrid() {
  var grid = $('#m-grid');
  grid.innerHTML = garmentFields().map(function (key) {
    var def = FIELDS[key];
    var v = mState.values[key];
    return '<div class="m-item" data-f="' + key + '"><label title="' + esc(def.label) + '">' + esc(def.label) + '</label>' +
      '<input inputmode="decimal" placeholder="—"' + (v === undefined ? '' : ' value="' + esc(toDisplay(v)) + '"') +
      ' aria-label="' + esc(def.label) + ' in ' + unitLabel() + '"><span class="unit">' + unitLabel() + '</span>' +
      '<div class="range-err">Between ' + (mState.unit === 'cm' ? +(def.min * IN_TO_CM).toFixed(0) : def.min) + '–' +
      (mState.unit === 'cm' ? +(def.max * IN_TO_CM).toFixed(0) : def.max) + ' ' + unitLabel() + '</div></div>';
  }).join('');
  $('#m-updated').textContent = currentClient.current_measurement && currentClient.current_measurement.updated_at
    ? 'Last saved ' + fmtDate(currentClient.current_measurement.updated_at) : 'No measurements saved yet';
  var cf = $('#custom-fields');
  cf.innerHTML = mState.custom.map(function (f, i) {
    var shown = (f.value === '' || f.value === undefined || f.value === null) ? '' : toDisplay(f.value);
    return '<div class="m-item" data-custom="' + i + '"><label>' + esc(f.name) + ' (custom)</label>' +
      '<input inputmode="decimal" placeholder="—" value="' + esc(shown) + '"' +
      ' data-custom-input="' + i + '"><span class="unit">' + unitLabel() + '</span></div>';
  }).join('') +
    '<div class="m-item"><label>Add custom field</label>' +
    '<div style="display:flex;gap:6px"><input id="cf-new-name" placeholder="Name" style="min-width:0"><button class="btn ghost small" id="cf-add" style="flex:none">＋</button></div></div>';
  bindFieldEvents();
}

function bindDesigner() {
  renderMeasureGrid();
  $('#garment-tabs').addEventListener('click', function (e) {
    var b = e.target.closest('[data-g]');
    if (!b) return;
    mState.garment = b.getAttribute('data-g');
    document.querySelectorAll('#garment-tabs button').forEach(function (x) {
      var on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-selected', on);
    });
    renderMeasureGrid();
  });
  $('#unit-toggle').addEventListener('click', function (e) {
    var b = e.target.closest('[data-u]');
    if (!b || b.classList.contains('active')) return;
    collectFormValues();              // canonical inches
    mState.unit = b.getAttribute('data-u');
    document.querySelectorAll('#unit-toggle button').forEach(function (x) { x.classList.toggle('active', x === b); });
    renderMeasureGrid();
  });
  $('#m-notes').addEventListener('input', function () { mState.notes = this.value; });
  $('#m-save').addEventListener('click', saveMeasurements);
  $('#body-panel').addEventListener('click', function (e) {
    var mk = e.target.closest('.marker');
    if (!mk) return;
    activateMarker(mk.getAttribute('data-mk'));
    var input = document.querySelector('.m-item[data-f="' + mk.getAttribute('data-mk') + '"] input');
    if (input) { input.scrollIntoView({ block: 'center', behavior: 'smooth' }); input.focus({ preventScroll: true }); }
  });
}

function bindFieldEvents() {
  document.querySelectorAll('#m-grid .m-item').forEach(function (item) {
    var key = item.getAttribute('data-f');
    var input = item.querySelector('input');
    input.addEventListener('focus', function () { activateMarker(key); });
    input.addEventListener('blur', function () { deactivateMarker(key); validateFieldInput(key, input); });
    input.addEventListener('input', function () { item.classList.remove('invalid'); });
  });
  document.querySelectorAll('[data-custom-input]').forEach(function (input) {
    input.addEventListener('focus', function () { input.closest('.m-item').classList.add('hot'); });
    input.addEventListener('blur', function () { input.closest('.m-item').classList.remove('hot'); });
    input.addEventListener('input', function () {
      var i = Number(input.getAttribute('data-custom-input'));
      var parsed = parseFloat(input.value);
      mState.custom[i].value = isNaN(parsed) ? '' : parsed;
    });
  });
  var add = $('#cf-add'); if (add) add.addEventListener('click', addCustomField);
  var nn = $('#cf-new-name');
  if (nn) nn.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addCustomField(); } });
}
function addCustomField() {
  var input = $('#cf-new-name');
  var name = (input.value || '').trim();
  if (!name) { input.focus(); return; }
  if (FIELDS[name.toLowerCase().replace(/\s+/g, '')]) { toast('That is already a standard field.', true); return; }
  if (mState.custom.length >= 10) { toast('Up to 10 custom fields.', true); return; }
  mState.custom.push({ name: name.slice(0, 40), value: '' });
  renderMeasureGrid();
  var last = document.querySelector('[data-custom-input="' + (mState.custom.length - 1) + '"]');
  if (last) last.focus();
}
function validateFieldInput(key, input) {
  var item = input.closest('.m-item');
  var raw = parseFloat(input.value);
  if (input.value.trim() === '') { item.classList.remove('invalid'); return; }
  var def = FIELDS[key];
  var inches = toInches(raw);
  var ok = !isNaN(inches) && inches >= def.min - 0.01 && inches <= def.max + 0.01;
  item.classList.toggle('invalid', !ok);
}
function activateMarker(key) {
  document.querySelectorAll('.marker.active').forEach(function (x) { x.classList.remove('active'); });
  var mk = document.querySelector('.marker[data-mk="' + key + '"]');
  if (mk) mk.classList.add('active');
}
function deactivateMarker(key) {
  var mk = document.querySelector('.marker[data-mk="' + key + '"]');
  if (mk) mk.classList.remove('active');
}

function collectFormValues() {
  /* Read all inputs into canonical inches; returns {ok, message}. */
  var values = {};
  var ok = true;
  document.querySelectorAll('#m-grid .m-item').forEach(function (item) {
    var key = item.getAttribute('data-f');
    var input = item.querySelector('input');
    var raw = parseFloat(input.value);
    if (input.value.trim() === '') return;
    var inches = toInches(raw);
    var def = FIELDS[key];
    if (isNaN(inches) || inches < def.min - 0.01 || inches > def.max + 0.01) {
      item.classList.add('invalid'); ok = false; return;
    }
    values[key] = +inches.toFixed(2);
  });
  mState.custom.forEach(function (f, i) {
    var input = document.querySelector('[data-custom-input="' + i + '"]');
    if (!input) return;
    var raw = parseFloat(input.value);
    if (input.value.trim() === '' || isNaN(raw)) return;
    var inches = toInches(raw);
    if (!(inches >= 0.2 && inches <= 400)) { item.closest('.m-item').classList.add('invalid'); ok = false; return; }
    values[f.name] = +inches.toFixed(2);
  });
  if (!ok) return { ok: false, message: 'Please fix the highlighted measurement values.' };
  if (Object.keys(values).length === 0) return { ok: false, message: 'Enter at least one measurement before saving.' };
  mState.values = values;
  mState.notes = $('#m-notes').value.trim();
  return { ok: true };
}

function saveMeasurements() {
  var check = collectFormValues();
  if (!check.ok) { toast(check.message, true); return; }
  var btn = $('#m-save');
  btn.disabled = true; btn.textContent = 'Saving…';
  api('POST', '/api/clients/' + encodeURIComponent(currentClient.client_id) + '/measurements', {
    garment_type: mState.garment,
    values: mState.values,
    notes: mState.notes,
    unit_used: mState.unit
  }).then(function () {
    toast('Measurements saved ✓ — previous session kept in history');
    renderClientPageRefresh();
  }).catch(function (err) {
    btn.disabled = false; btn.textContent = '💾 Save measurements';
    toast(err.message, true);
  });
}
function renderClientPageRefresh() {
  if (location.hash.indexOf('#/client/') === 0) {
    var cid = currentClient.client_id;
    view.innerHTML = '<div class="empty">Refreshing…</div>';
    renderClientPage(cid);
  }
}

/* ---------- history panel + compare ---------- */
function renderHistoryPanel() {
  var panel = $('#history-panel');
  if (!panel) return;
  var hist = mState.history;
  var options = hist.map(function (h) { return '<option value="' + esc(h.id) + '">' + esc(fmtDate(h.saved_at)) + ' · ' + esc(h.garment_type) + '</option>'; }).join('');
  panel.innerHTML =
    '<div class="panel-h"><h3>Measurement history</h3>' +
      '<span class="updated">' + hist.length + ' session' + (hist.length === 1 ? '' : 's') + ' · nothing is ever overwritten</span></div>' +
    '<div class="panel-b">';
  if (!hist.length) {
    panel.innerHTML += '<div class="empty">No sessions yet — save the first measurement above.</div></div>';
    return;
  }
  panel.innerHTML +=
    '<div class="hist-list">' + hist.map(function (h, i) {
      var keys = Object.keys(h.values || {}).slice(0, 6).map(function (k) { return '<b>' + esc(k) + '</b> ' + h.values[k] + '″'; }).join(' · ');
      return '<div class="hist-item" data-hs="' + esc(h.id) + '">' +
        '<span class="badge info">' + esc(h.garment_type) + '</span>' +
        '<span class="when">' + esc(fmtDate(h.saved_at)) + '</span>' +
        '<span class="meta">' + keys + (h.notes ? ' · <i>' + esc(h.notes) + '</i>' : '') + '</span>' +
        '<button class="btn ghost small" data-load="' + esc(h.id) + '">Load into form</button></div>';
    }).join('') + '</div>' +
    (hist.length >= 1 ?
      '<div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line)">' +
      '<h4 style="font:400 14px var(--serif);margin:0 0 10px">Compare sessions</h4>' +
      '<div class="form-row" style="grid-template-columns:1fr 1fr 1fr;align-items:end;gap:10px">' +
      '<div class="field" style="margin:0"><label>Old session</label><select id="cmp-a"><option value="">— choose —</option>' + options + '</select></div>' +
      '<div class="field" style="margin:0"><label>New</label><select id="cmp-b"><option value="">Current form</option>' + options + '</select></div>' +
      '<button class="btn ghost" id="cmp-go">Compare →</button></div><div id="cmp-out" style="margin-top:12px"></div></div>' : '') +
    '</div>';
  panel.addEventListener('click', function (e) {
    var load = e.target.closest('[data-load]');
    if (load) { loadSession(load.getAttribute('data-load')); return; }
    var item = e.target.closest('.hist-item');
    if (item && !e.target.closest('button')) loadSession(item.getAttribute('data-hs'));
  });
  var go = $('#cmp-go');
  if (go) go.addEventListener('click', compareSessions);
}
function findSession(id) {
  return mState.history.filter(function (h) { return h.id === id; })[0] || null;
}
function loadSession(id) {
  var h = findSession(id);
  if (!h) return;
  mState.garment = h.garment_type;
  mState.values = Object.assign({}, h.values);
  mState.notes = h.notes || '';
  mState.unit = h.unit_used || 'in';
  mState.custom = Object.keys(h.values || {}).filter(function (k) { return !FIELDS[k]; })
    .map(function (k) { return { name: k, value: h.values[k] }; });
  /* refresh tabs + unit toggle + grid */
  document.querySelectorAll('#garment-tabs button').forEach(function (x) {
    var on = x.getAttribute('data-g') === mState.garment;
    x.classList.toggle('active', on); x.setAttribute('aria-selected', on);
  });
  document.querySelectorAll('#unit-toggle button').forEach(function (x) { x.classList.toggle('active', x.getAttribute('data-u') === mState.unit); });
  $('#m-notes').value = mState.notes;
  renderMeasureGrid();
  toast('Loaded session from ' + fmtDate(h.saved_at) + ' — adjust and save to create a new session');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function compareSessions() {
  var aId = $('#cmp-a').value, bId = $('#cmp-b').value;
  if (!aId && !bId) { toast('Pick at least one session to compare.', true); return; }
  collectFormValues();
  var a = aId ? findSession(aId) : null;
  var b = bId ? findSession(bId) : { values: mState.values, label: 'Current form' };
  var aVals = a ? a.values : {}, bVals = b ? b.values : {};
  var keys = {};
  Object.keys(aVals).forEach(function (k) { keys[k] = 1; });
  Object.keys(bVals).forEach(function (k) { keys[k] = 1; });
  var rows = Object.keys(keys).sort().map(function (k) {
    var av = aVals[k], bv = bVals[k];
    var cls = '', delta = '';
    if (av !== undefined && bv !== undefined) {
      var d = +(bv - av).toFixed(2);
      delta = (d > 0 ? '+ ' : d < 0 ? '− ' : '') + Math.abs(d) + '″';
      cls = d > 0 ? 'up' : d < 0 ? 'down' : 'same';
      if (Math.abs(d) > 2) cls += ''; 
    } else if (av !== undefined) { delta = 'removed'; cls = 'same'; }
    else { delta = 'new'; cls = 'same'; }
    return '<tr><td><b>' + esc(FIELDS[k] ? FIELDS[k].label : k) + '</b></td>' +
      '<td class="num">' + (av === undefined ? '—' : av + '″') + '</td>' +
      '<td class="num">' + (bv === undefined ? '—' : bv + '″') + '</td>' +
      '<td class="num ' + cls + '">' + esc(delta) + '</td></tr>';
  }).join('');
  $('#cmp-out').innerHTML = '<table class="table cmp-table"><thead><tr><th>Measurement</th><th>Old' +
    (a ? ' · ' + esc(fmtDate(a.saved_at)) : '') + '</th><th>New' + (bId ? '' : ' (form)') + '</th><th>Change</th></tr></thead><tbody>' +
    rows + '</tbody></table>';
}

/* ---------- client orders ---------- */
function loadClientOrders() {
  var panel = $('#client-orders');
  api('GET', '/api/clients/' + encodeURIComponent(currentClient.client_id) + '/orders').then(function (data) {
    var orders = data.orders || [];
    panel.innerHTML = '<div class="panel-h"><h3>Orders</h3>' +
      '<button class="btn ghost small" id="co-add">＋ New order</button></div>';
    panel.innerHTML += orders.length ? ordersTable(orders) : '<div class="empty">No orders for this customer yet.</div>';
    bindOrderTable(panel);
    $('#co-add').addEventListener('click', function () { openAddOrder(currentClient.client_id, currentClient.name); });
  });
}

/* ============================================================
   ORDERS
   ============================================================ */
function ordersTable(orders) {
  var clientNames = orders._names || {};
  var rows = orders.map(function (o) {
    var badge = o.status === 'Delivered' ? 'ok' : o.status === 'Cancelled' ? 'bad' :
                o.status === 'Ready' ? 'info' : 'warn';
    var overdue = o.due_date && o.due_date < todayISO() && (o.status === 'Pending' || o.status === 'In Progress');
    return '<tr><td><b>' + esc(o.garment) + '</b>' + (o.notes ? '<span class="sub">' + esc(o.notes) + '</span>' : '') + '</td>' +
      '<td data-client="' + esc(o.client_id) + '">' + esc(orders._names ? (orders._names[o.client_id] || o.client_id) : '') + '</td>' +
      '<td style="white-space:nowrap">' + esc(o.due_date || '—') + (overdue ? ' <span class="badge bad">overdue</span>' : '') + '</td>' +
      '<td><select data-status="' + esc(o.order_id) + '" class="badge ' + badge + '" style="border:1px solid var(--line);background:var(--panel-2);padding:5px 8px;border-radius:999px;font-size:10.5px;font-weight:700">' +
        ['Pending', 'In Progress', 'Ready', 'Delivered', 'Cancelled'].map(function (s) {
          return '<option ' + (s === o.status ? 'selected' : '') + '>' + s + '</option>'; }).join('') +
      '</select></td>' +
      '<td style="color:var(--muted);font-size:12px;white-space:nowrap">' + esc(fmtDate(o.created_at)) + '</td></tr>';
  }).join('');
  return '<table class="table"><thead><tr><th>Garment</th><th>Customer</th><th>Due date</th><th>Status</th><th>Created</th></tr></thead><tbody>' + rows + '</tbody></table>';
}
function bindOrderTable(scope) {
  scope.querySelectorAll('[data-status]').forEach(function (sel) {
    sel.addEventListener('change', function () {
      api('PUT', '/api/orders/' + encodeURIComponent(sel.getAttribute('data-status')), { status: sel.value })
        .then(function () { toast('Order marked ' + sel.value + ' ✓'); })
        .catch(function (err) { toast(err.message, true); route(); });
    });
  });
}
function renderOrders() {
  view.innerHTML = '<div class="panel"><div class="panel-h"><h3>Orders</h3>' +
    '<button class="btn" id="add-order">＋ New order</button></div>' +
    '<div id="orders-list"><div class="empty">Loading…</div></div></div>';
  $('#add-order').addEventListener('click', function () { openAddOrder(); });
  return Promise.all([api('GET', '/api/orders'), api('GET', '/api/clients')]).then(function (res) {
    var orders = res[0].orders || [];
    var names = {};
    (res[1].clients || []).forEach(function (c) { names[c.client_id] = c.name; });
    orders._names = names;
    var wrap = $('#orders-list');
    wrap.innerHTML = orders.length ? ordersTable(orders) : '<div class="empty">No orders yet — create the first one.</div>';
    bindOrderTable(wrap);
  }).catch(function (err) { $('#orders-list').innerHTML = '<div class="empty">' + esc(err.message) + '</div>'; });
}
function openAddOrder(clientId, clientName) {
  return api('GET', '/api/clients').then(function (data) {
    var clients = data.clients || [];
    if (!clients.length) { toast('Add a customer before creating an order.', true); return; }
    var options = clients.map(function (c) {
      return '<option value="' + esc(c.client_id) + '"' + (c.client_id === clientId ? ' selected' : '') + '>' +
        esc(c.name) + ' · ' + esc(c.phone) + '</option>';
    }).join('');
    var back = modal('New order',
      '<div class="field"><label>Customer *</label><select id="of-client">' + options + '</select></div>' +
      '<div class="form-row"><div class="field"><label>Garment *</label><input id="of-garment" maxlength="80" placeholder="e.g. Blouse, Lehenga…" value="' + esc(clientName && clientId ? '' : '') + '"></div>' +
      '<div class="field"><label>Due date</label><input id="of-due" type="date" min="' + todayISO() + '"></div></div>' +
      '<div class="field"><label>Notes</label><textarea id="of-notes" maxlength="500" placeholder="Design, fabric, special instructions…"></textarea></div>',
      '<button class="btn ghost" data-close>Cancel</button><button class="btn" id="of-save">Create order</button>');
    $('#of-save', back).addEventListener('click', function () {
      var payload = {
        client_id: $('#of-client').value,
        garment: $('#of-garment').value.trim(),
        due_date: $('#of-due').value,
        notes: $('#of-notes').value.trim()
      };
      if (!payload.garment) { $('#of-garment').closest('.field').classList.add('invalid'); return; }
      $('#of-save').disabled = true;
      api('POST', '/api/orders', payload).then(function () {
        closeModal(); toast('Order created ✓'); route();
      }).catch(function (err) { $('#of-save').disabled = false; toast(err.message, true); });
    });
  }).catch(function (err) { toast(err.message, true); });
}

/* ============================================================
   APPOINTMENTS
   ============================================================ */
function renderAppointments() {
  view.innerHTML = '<div class="panel"><div class="panel-h"><h3>Appointment requests</h3>' +
    '<span class="updated">From the public website booking form</span></div>' +
    '<div id="appt-list"><div class="empty">Loading…</div></div></div>';
  return api('GET', '/api/appointments').then(function (data) {
    var appts = data.appointments || [];
    var wrap = $('#appt-list');
    if (!appts.length) {
      wrap.innerHTML = '<div class="empty">No appointment requests yet.<br>Bookings from the website will appear here instantly.</div>';
      return;
    }
    var today = todayISO();
    var rows = appts.map(function (a) {
      var badge = a.status === 'Confirmed' ? 'ok' : a.status === 'Cancelled' ? 'bad' : 'warn';
      return '<tr><td><b>' + esc(a.name) + '</b><span class="sub">' + esc(a.phone) + '</span></td>' +
        '<td>' + esc(a.service) + '</td>' +
        '<td style="white-space:nowrap">' + esc(a.date) + ' at ' + esc(a.time) +
        (a.date === today ? ' <span class="badge ok">today</span>' : '') + '</td>' +
        '<td style="max-width:220px;font-size:12.5px;color:var(--muted)">' + esc(a.notes || '—') + '</td>' +
        '<td><span class="badge ' + badge + '">' + esc(a.status) + '</span></td>' +
        '<td style="color:var(--muted);font-size:12px;white-space:nowrap">' + esc(fmtDate(a.received_at)) + '</td></tr>';
    }).join('');
    wrap.innerHTML = '<table class="table"><thead><tr><th>Customer</th><th>Service</th><th>When</th><th>Query</th><th>Status</th><th>Received</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }).catch(function (err) { $('#appt-list').innerHTML = '<div class="empty">' + esc(err.message) + '</div>'; });
}

/* ============================================================
   SETTINGS
   ============================================================ */
function renderSettings() {
  view.innerHTML = '<div class="empty">Loading…</div>';
  return api('GET', '/api/me').then(function (me) {
    view.innerHTML =
      '<div class="grid-2" style="margin-top:0">' +
      '<div class="panel"><div class="panel-h"><h3>Account & session</h3></div><div class="panel-b"><dl class="kv">' +
        '<dt>Signed in as</dt><dd><b>' + esc(me.username) + '</b> (' + esc(me.role) + ')</dd>' +
        '<dt>Session expires</dt><dd>Managed securely by the server (automatic expiry)</dd>' +
        '<dt>Storage backend</dt><dd>' + esc(me.storage) + (me.sheets ? ' · <span class="badge ok">Google Sheets connected</span>' : ' · <span class="badge warn">local SQLite — set GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_SHEET_ID to use Sheets</span>') + '</dd>' +
      '</dl></div></div>' +
      '<div class="panel"><div class="panel-h"><h3>Appearance</h3></div><div class="panel-b">' +
        '<p style="margin:0 0 12px;color:var(--muted);font-size:13px">Choose light (boutique cream) or dark (evening tailoring) theme.</p>' +
        '<button class="btn ghost" id="set-theme">Toggle light / dark</button></div></div>' +
      '</div>' +
      '<div class="panel" style="margin-top:14px"><div class="panel-h"><h3>Changing the admin password</h3></div><div class="panel-b" style="font-size:13.5px;color:var(--muted);line-height:1.8">' +
        'The password lives only in the server environment — never in this page or in the code.<br>' +
        '1) On the server run <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px">python hash_password.py</code><br>' +
        '2) Set <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px">ADMIN_PASSWORD_HASH</code> to the printed value and restart the service.<br>' +
        'Best practice: also set a long random <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px">SESSION_SECRET</code>.' +
      '</div></div>';
    $('#set-theme').addEventListener('click', function () { $('#theme-btn').click(); });
  });
}

/* ============================================================
   BOOT
   ============================================================ */
api('GET', '/api/me').then(function (me) {
  authenticated = true;
  $('#who').textContent = me.username || 'admin';
  route();
}).catch(function () {
  if (!$('#gate')) showGate();
});
})();
