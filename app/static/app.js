// lakehouse-ui frontend — worksheets, catalog browser, history, whoami.
// No framework; CodeMirror 5 (loaded via <script> tags in index.html) for
// SQL syntax highlighting only (no autocomplete).

let worksheets = [];
let activeWorksheetId = null;
let nextWorksheetNumber = 1;
let editor = null;

function newWorksheet(sql = '') {
  const id = 'ws-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
  const worksheet = {
    id,
    title: 'Worksheet ' + nextWorksheetNumber++,
    sql,
    columns: [],
    rows: [],
    error: null,
  };
  worksheets.push(worksheet);
  return worksheet;
}

function activateWorksheet(id) {
  if (activeWorksheetId) {
    const current = worksheets.find((w) => w.id === activeWorksheetId);
    if (current) current.sql = editor.getValue();
  }
  activeWorksheetId = id;
  const worksheet = worksheets.find((w) => w.id === id);
  editor.setValue(worksheet.sql);
  renderTabs();
  renderResults(worksheet);
}

function closeWorksheet(id) {
  const index = worksheets.findIndex((w) => w.id === id);
  if (index === -1) return;
  worksheets.splice(index, 1);
  if (worksheets.length === 0) {
    const fresh = newWorksheet();
    activateWorksheet(fresh.id);
    return;
  }
  if (activeWorksheetId === id) {
    const next = worksheets[Math.max(0, index - 1)];
    activateWorksheet(next.id);
  } else {
    renderTabs();
  }
}

function renderTabs() {
  const container = document.getElementById('worksheet-tabs');
  container.innerHTML = '';
  worksheets.forEach((w) => {
    const tab = document.createElement('div');
    tab.className = 'worksheet-tab' + (w.id === activeWorksheetId ? ' active' : '');

    const title = document.createElement('span');
    title.textContent = w.title;
    title.addEventListener('click', () => activateWorksheet(w.id));

    const close = document.createElement('span');
    close.className = 'worksheet-tab-close';
    close.textContent = '✕';
    close.addEventListener('click', (e) => {
      e.stopPropagation();
      closeWorksheet(w.id);
    });

    tab.appendChild(title);
    tab.appendChild(close);
    container.appendChild(tab);
  });

  const addButton = document.createElement('div');
  addButton.className = 'worksheet-tab-add';
  addButton.textContent = '+';
  addButton.addEventListener('click', () => {
    const fresh = newWorksheet();
    activateWorksheet(fresh.id);
  });
  container.appendChild(addButton);
}

function renderResults(worksheet) {
  const errorBox = document.getElementById('error');
  const table = document.getElementById('results');
  table.innerHTML = '';
  errorBox.textContent = '';
  if (worksheet.error) {
    errorBox.textContent = worksheet.error;
    return;
  }
  if (!worksheet.columns.length) return;

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  worksheet.columns.forEach((c) => {
    const th = document.createElement('th');
    th.textContent = c;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  worksheet.rows.forEach((row) => {
    const tr = document.createElement('tr');
    row.forEach((value) => {
      const td = document.createElement('td');
      td.textContent = value === null ? 'NULL' : String(value);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
}

async function runActiveWorksheet() {
  const worksheet = worksheets.find((w) => w.id === activeWorksheetId);
  if (!worksheet) return;
  worksheet.sql = editor.getValue();
  const sql = worksheet.sql.trim();
  if (!sql) return;

  const statusEl = document.getElementById('query-status');
  const runButton = document.getElementById('run-btn');
  statusEl.textContent = 'Running...';
  runButton.disabled = true;

  try {
    let response, body;
    try {
      response = await fetch('/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      });
      body = await response.json();
    } catch (err) {
      worksheet.error = 'Request failed: ' + err;
      worksheet.columns = [];
      worksheet.rows = [];
      renderResults(worksheet);
      return;
    }

    if (response.status === 401) {
      window.location.href = '/login';
      return;
    }

    if (!response.ok) {
      worksheet.error = body.detail || 'Query failed';
      worksheet.columns = [];
      worksheet.rows = [];
    } else {
      worksheet.error = null;
      worksheet.columns = body.columns;
      worksheet.rows = body.rows;
    }
    renderResults(worksheet);
    loadHistory();
  } finally {
    statusEl.textContent = '';
    runButton.disabled = false;
  }
}

// --- Catalog tree -----------------------------------------------------

async function loadCatalog() {
  const tree = document.getElementById('catalog-tree');
  tree.textContent = 'Loading...';
  let namespaces;
  try {
    const response = await fetch('/catalog/namespaces');
    if (response.status === 401) {
      window.location.href = '/login';
      return;
    }
    namespaces = (await response.json()).namespaces;
  } catch (err) {
    tree.textContent = 'Failed to load catalog: ' + err;
    return;
  }

  tree.innerHTML = '';
  namespaces.forEach((ns) => {
    const nsEl = document.createElement('div');
    nsEl.className = 'tree-namespace';
    nsEl.textContent = '▸ ' + ns;

    const tablesEl = document.createElement('div');
    tablesEl.className = 'tree-tables';
    tablesEl.hidden = true;
    let loaded = false;

    nsEl.addEventListener('click', async () => {
      tablesEl.hidden = !tablesEl.hidden;
      nsEl.textContent = (tablesEl.hidden ? '▸ ' : '▾ ') + ns;
      if (!loaded) {
        loaded = true;
        const response = await fetch('/catalog/tables/' + encodeURIComponent(ns));
        const body = await response.json();
        body.tables.forEach((t) => {
          const tEl = document.createElement('div');
          tEl.className = 'tree-table';
          tEl.textContent = t;
          tEl.addEventListener('click', (e) => {
            e.stopPropagation();
            editor.replaceSelection('lakehouse.' + ns + '.' + t);
            editor.focus();
          });
          tablesEl.appendChild(tEl);
        });
      }
    });

    tree.appendChild(nsEl);
    tree.appendChild(tablesEl);
  });
}

// --- History ------------------------------------------------------------

async function loadHistory() {
  const list = document.getElementById('history-list');
  let history;
  try {
    const response = await fetch('/history');
    if (response.status === 401) {
      window.location.href = '/login';
      return;
    }
    history = (await response.json()).history;
  } catch (err) {
    list.textContent = 'Failed to load history: ' + err;
    return;
  }

  list.innerHTML = '';
  history.forEach((item) => {
    const el = document.createElement('div');
    el.className = 'history-item' + (item.status === 'error' ? ' history-item-error' : '');
    const preview = item.sql_text.length > 60 ? item.sql_text.slice(0, 60) + '…' : item.sql_text;
    el.textContent = preview;
    el.title = item.sql_text + '\n' + item.run_at;
    el.addEventListener('click', () => {
      const fresh = newWorksheet(item.sql_text);
      activateWorksheet(fresh.id);
    });
    list.appendChild(el);
  });
}

// --- Sidebar panel switching ----------------------------------------------

function showSidebarPanel(panel) {
  document.getElementById('catalog-tree').hidden = panel !== 'catalog';
  document.getElementById('history-list').hidden = panel !== 'history';
  document.getElementById('tab-catalog').classList.toggle('active', panel === 'catalog');
  document.getElementById('tab-history').classList.toggle('active', panel === 'history');
  if (panel === 'history') loadHistory();
}

// --- Whoami / logout --------------------------------------------------

async function loadWhoami() {
  const response = await fetch('/me');
  if (response.status === 401) {
    window.location.href = '/login';
    return;
  }
  const body = await response.json();
  document.getElementById('whoami-text').textContent = body.principal + ' · ' + body.roles.join(', ');
}

async function logout() {
  await fetch('/logout', { method: 'POST' });
  window.location.href = '/login';
}

// --- Init ---------------------------------------------------------------

function init() {
  editor = CodeMirror(document.getElementById('editor-container'), {
    mode: 'text/x-sql',
    theme: 'dracula',
    lineNumbers: true,
    value: '',
  });
  editor.setOption('extraKeys', {
    'Cmd-Enter': runActiveWorksheet,
    'Ctrl-Enter': runActiveWorksheet,
  });

  const first = newWorksheet();
  activeWorksheetId = first.id;
  renderTabs();

  document.getElementById('run-btn').addEventListener('click', runActiveWorksheet);
  document.getElementById('tab-catalog').addEventListener('click', () => showSidebarPanel('catalog'));
  document.getElementById('tab-history').addEventListener('click', () => showSidebarPanel('history'));
  document.getElementById('refresh-catalog').addEventListener('click', loadCatalog);
  document.getElementById('logout-btn').addEventListener('click', logout);

  loadWhoami();
  loadCatalog();
}

init();
