// lakehouse-ui frontend — worksheets, catalog browser, history, whoami.
// No framework; CodeMirror 5 (loaded via <script> tags in index.html) for
// SQL syntax highlighting only (no autocomplete).

let worksheets = [];
let activeWorksheetId = null;
let nextWorksheetNumber = 1;
let editor = null;
let queryInFlight = false;

// Shared fetch wrapper: redirects to /login on 401, throws a descriptive
// Error on any other non-2xx or malformed response, otherwise returns the
// parsed JSON body. Every loader below should go through this instead of
// hand-rolling its own fetch/response.ok/json() handling — that
// inconsistency (some loaders checked response.ok, some didn't, one had
// no try/catch at all) is exactly what let real bugs through review.
async function apiFetch(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    throw new Error('Request failed: ' + err);
  }
  if (response.status === 401) {
    window.location.href = '/login';
    // Throw anyway so callers' code after this line never executes with
    // no valid body — the redirect above is already in flight.
    throw new Error('not authenticated');
  }
  let body;
  try {
    body = await response.json();
  } catch (err) {
    throw new Error('Server returned an invalid response');
  }
  if (!response.ok) {
    throw new Error(body.detail || 'Request failed');
  }
  return body;
}

function openModal(html) {
  const overlay = document.getElementById('modal-overlay');
  const box = document.getElementById('modal-box');
  box.innerHTML = html;
  overlay.hidden = false;
}

function closeModal() {
  document.getElementById('modal-overlay').hidden = true;
  document.getElementById('modal-box').innerHTML = '';
}

function openCreateDatasetModal() {
  openModal(`
    <h3>New dataset</h3>
    <label for="new-dataset-name">Dataset name</label>
    <input type="text" id="new-dataset-name" autocomplete="off">
    <div class="modal-error" id="new-dataset-error"></div>
    <div class="modal-actions">
      <button class="secondary" id="new-dataset-cancel" type="button">Cancel</button>
      <button class="primary" id="new-dataset-create" type="button">Create</button>
    </div>
  `);
  document.getElementById('new-dataset-cancel').addEventListener('click', closeModal);
  document.getElementById('new-dataset-create').addEventListener('click', submitCreateDataset);
  document.getElementById('new-dataset-name').focus();
}

async function submitCreateDataset() {
  const nameInput = document.getElementById('new-dataset-name');
  const errorEl = document.getElementById('new-dataset-error');
  const name = nameInput.value.trim();
  if (!name) {
    errorEl.textContent = 'Dataset name is required.';
    return;
  }
  try {
    await apiFetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql: `CREATE SCHEMA lakehouse.${name}` }),
    });
  } catch (err) {
    if (err.message === 'not authenticated') return;
    errorEl.textContent = err.message;
    return;
  }
  closeModal();
  loadCatalog();
}

function confirmDeleteDataset(name) {
  openModal(`
    <h3>Delete dataset "${name}"?</h3>
    <p>This deletes the dataset and everything in it. This cannot be undone.</p>
    <div class="modal-error" id="delete-dataset-error"></div>
    <div class="modal-actions">
      <button class="secondary" id="delete-dataset-cancel" type="button">Cancel</button>
      <button class="primary" id="delete-dataset-confirm" type="button">Delete</button>
    </div>
  `);
  document.getElementById('delete-dataset-cancel').addEventListener('click', closeModal);
  document.getElementById('delete-dataset-confirm').addEventListener('click', () => submitDeleteDataset(name));
}

async function submitDeleteDataset(name) {
  const errorEl = document.getElementById('delete-dataset-error');
  try {
    await apiFetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql: `DROP SCHEMA lakehouse.${name}` }),
    });
  } catch (err) {
    if (err.message === 'not authenticated') return;
    errorEl.textContent = err.message;
    return;
  }
  closeModal();
  loadCatalog();
}

let newTableColumnCount = 0;

function openCreateTableModal(namespace) {
  newTableColumnCount = 0;
  openModal(`
    <h3>New table in ${namespace}</h3>
    <label for="new-table-name">Table name</label>
    <input type="text" id="new-table-name" autocomplete="off">
    <label>Columns</label>
    <div id="new-table-columns"></div>
    <button class="secondary" id="new-table-add-column" type="button" style="margin-top:0.4rem;">+ Add column</button>
    <div class="modal-error" id="new-table-error"></div>
    <div class="modal-actions">
      <button class="secondary" id="new-table-cancel" type="button">Cancel</button>
      <button class="primary" id="new-table-create" type="button">Create</button>
    </div>
  `);
  document.getElementById('new-table-cancel').addEventListener('click', closeModal);
  document.getElementById('new-table-add-column').addEventListener('click', addNewTableColumnRow);
  document.getElementById('new-table-create').addEventListener('click', () => submitCreateTable(namespace));
  addNewTableColumnRow();
  document.getElementById('new-table-name').focus();
}

function addNewTableColumnRow() {
  const id = 'col-' + newTableColumnCount++;
  const row = document.createElement('div');
  row.className = 'new-table-column-row';
  row.id = id;
  row.innerHTML = `
    <input type="text" class="new-table-col-name" placeholder="column_name">
    <select class="new-table-col-type">
      <option value="VARCHAR">VARCHAR</option>
      <option value="BIGINT">BIGINT</option>
      <option value="INTEGER">INTEGER</option>
      <option value="DOUBLE">DOUBLE</option>
      <option value="BOOLEAN">BOOLEAN</option>
      <option value="DATE">DATE</option>
      <option value="TIMESTAMP">TIMESTAMP</option>
    </select>
    <label class="new-table-col-required"><input type="checkbox"> NOT NULL</label>
    <button type="button" class="new-table-col-remove">✕</button>
  `;
  row.querySelector('.new-table-col-remove').addEventListener('click', () => row.remove());
  document.getElementById('new-table-columns').appendChild(row);
}

async function submitCreateTable(namespace) {
  const errorEl = document.getElementById('new-table-error');
  const name = document.getElementById('new-table-name').value.trim();
  if (!name) {
    errorEl.textContent = 'Table name is required.';
    return;
  }
  const rows = Array.from(document.querySelectorAll('#new-table-columns .new-table-column-row'));
  if (rows.length === 0) {
    errorEl.textContent = 'At least one column is required.';
    return;
  }
  const columnDefs = [];
  for (const row of rows) {
    const colName = row.querySelector('.new-table-col-name').value.trim();
    const colType = row.querySelector('.new-table-col-type').value;
    const required = row.querySelector('.new-table-col-required input').checked;
    if (!colName) {
      errorEl.textContent = 'Every column needs a name.';
      return;
    }
    columnDefs.push(`${colName} ${colType}${required ? ' NOT NULL' : ''}`);
  }
  const sql = `CREATE TABLE lakehouse.${namespace}.${name} (${columnDefs.join(', ')})`;
  try {
    await apiFetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql }),
    });
  } catch (err) {
    if (err.message === 'not authenticated') return;
    errorEl.textContent = err.message;
    return;
  }
  closeModal();
  loadCatalog();
}

function confirmDeleteTable(namespace, table) {
  openModal(`
    <h3>Delete table "${namespace}.${table}"?</h3>
    <p>This cannot be undone.</p>
    <div class="modal-error" id="delete-table-error"></div>
    <div class="modal-actions">
      <button class="secondary" id="delete-table-cancel" type="button">Cancel</button>
      <button class="primary" id="delete-table-confirm" type="button">Delete</button>
    </div>
  `);
  document.getElementById('delete-table-cancel').addEventListener('click', closeModal);
  document.getElementById('delete-table-confirm').addEventListener('click', () => submitDeleteTable(namespace, table));
}

async function submitDeleteTable(namespace, table) {
  const errorEl = document.getElementById('delete-table-error');
  try {
    await apiFetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql: `DROP TABLE lakehouse.${namespace}.${table}` }),
    });
  } catch (err) {
    if (err.message === 'not authenticated') return;
    errorEl.textContent = err.message;
    return;
  }
  closeModal();
  loadCatalog();
}

async function openTableDetailsModal(namespace, table) {
  openModal('<h3>' + namespace + '.' + table + '</h3><p>Loading…</p>');
  let details;
  try {
    details = await apiFetch(
      '/catalog/tables/' + encodeURIComponent(namespace) + '/' + encodeURIComponent(table) + '/details'
    );
  } catch (err) {
    if (err.message === 'not authenticated') return;
    openModal(
      '<h3>' + namespace + '.' + table + '</h3><p class="modal-error">' + err.message +
      '</p><div class="modal-actions"><button class="secondary" id="details-close" type="button">Close</button></div>'
    );
    document.getElementById('details-close').addEventListener('click', closeModal);
    return;
  }
  const snap = details.current_snapshot;
  const rowsHtml = `
    <div class="detail-row"><span class="detail-label">Location</span><span class="detail-value">${details.location || '—'}</span></div>
    <div class="detail-row"><span class="detail-label">Last updated</span><span class="detail-value">${details.last_updated_ms ? new Date(details.last_updated_ms).toISOString() : '—'}</span></div>
    <div class="detail-row"><span class="detail-label">Rows</span><span class="detail-value">${snap ? Number(snap.total_records).toLocaleString() : '—'}</span></div>
    <div class="detail-row"><span class="detail-label">Data files</span><span class="detail-value">${snap ? snap.total_data_files : '—'}</span></div>
  `;
  const schemaRows = details.fields
    .map((f) => `<tr><td>${f.name}</td><td>${f.type}</td><td>${f.required ? 'NOT NULL' : ''}</td></tr>`)
    .join('');
  openModal(`
    <h3>${namespace}.${table}</h3>
    ${rowsHtml}
    <table class="detail-schema-table"><thead><tr><th>Column</th><th>Type</th><th></th></tr></thead><tbody>${schemaRows}</tbody></table>
    <div class="modal-actions"><button class="secondary" id="details-close" type="button">Close</button></div>
  `);
  document.getElementById('details-close').addEventListener('click', closeModal);
}

async function openNamespaceDetailsModal(namespace) {
  openModal('<h3>' + namespace + '</h3><p>Loading…</p>');
  let details;
  try {
    details = await apiFetch('/catalog/namespaces/' + encodeURIComponent(namespace) + '/details');
  } catch (err) {
    if (err.message === 'not authenticated') return;
    openModal(
      '<h3>' + namespace + '</h3><p class="modal-error">' + err.message +
      '</p><div class="modal-actions"><button class="secondary" id="ns-details-close" type="button">Close</button></div>'
    );
    document.getElementById('ns-details-close').addEventListener('click', closeModal);
    return;
  }
  openModal(`
    <h3>${namespace}</h3>
    <div class="detail-row"><span class="detail-label">Location</span><span class="detail-value">${(details.properties && details.properties.location) || '—'}</span></div>
    <div class="detail-row"><span class="detail-label">Tables</span><span class="detail-value">${details.table_count}</span></div>
    <div class="modal-actions"><button class="secondary" id="ns-details-close" type="button">Close</button></div>
  `);
  document.getElementById('ns-details-close').addEventListener('click', closeModal);
}

function openSaveAsTableModal() {
  const worksheet = worksheets.find((w) => w.id === activeWorksheetId);
  if (!worksheet || !worksheet.columns.length) return;
  openModal(`
    <h3>Save results as table</h3>
    <label for="save-table-namespace">Dataset</label>
    <input type="text" id="save-table-namespace" autocomplete="off" placeholder="nyc_taxi">
    <label for="save-table-name">Table name</label>
    <input type="text" id="save-table-name" autocomplete="off">
    <div class="modal-error" id="save-table-error"></div>
    <div class="modal-actions">
      <button class="secondary" id="save-table-cancel" type="button">Cancel</button>
      <button class="primary" id="save-table-create" type="button">Save</button>
    </div>
  `);
  document.getElementById('save-table-cancel').addEventListener('click', closeModal);
  document.getElementById('save-table-create').addEventListener('click', submitSaveAsTable);
  document.getElementById('save-table-namespace').focus();
}

async function submitSaveAsTable() {
  const worksheet = worksheets.find((w) => w.id === activeWorksheetId);
  const errorEl = document.getElementById('save-table-error');
  const namespace = document.getElementById('save-table-namespace').value.trim();
  const name = document.getElementById('save-table-name').value.trim();
  if (!namespace || !name) {
    errorEl.textContent = 'Dataset and table name are both required.';
    return;
  }
  const originalSql = worksheet.sql.trim();
  const sql = `CREATE TABLE lakehouse.${namespace}.${name} AS ${originalSql}`;
  try {
    await apiFetch('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql }),
    });
  } catch (err) {
    if (err.message === 'not authenticated') return;
    errorEl.textContent = err.message;
    return;
  }
  closeModal();
  loadCatalog();
}

function newWorksheet(sql = '') {
  const id = 'ws-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
  const worksheet = {
    id,
    title: 'Worksheet ' + nextWorksheetNumber++,
    sql,
    columns: [],
    rows: [],
    error: null,
    truncated: false,
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
  const noticeBox = document.getElementById('result-notice');
  const table = document.getElementById('results');
  table.innerHTML = '';
  errorBox.textContent = '';
  noticeBox.hidden = true;
  noticeBox.textContent = '';
  if (worksheet.error) {
    errorBox.textContent = worksheet.error;
    return;
  }
  if (worksheet.truncated) {
    noticeBox.hidden = false;
    noticeBox.textContent =
      `Showing the first ${worksheet.rows.length.toLocaleString()} rows — ` +
      'the query returned more. Add a LIMIT to see a different slice.';
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
  if (queryInFlight) return;
  queryInFlight = true;

  const worksheet = worksheets.find((w) => w.id === activeWorksheetId);
  if (!worksheet) {
    queryInFlight = false;
    return;
  }
  worksheet.sql = editor.getValue();
  const sql = worksheet.sql.trim();
  if (!sql) {
    queryInFlight = false;
    return;
  }

  const statusEl = document.getElementById('query-status');
  const runButton = document.getElementById('run-btn');
  statusEl.textContent = 'Running...';
  runButton.disabled = true;

  try {
    let body;
    try {
      body = await apiFetch('/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      });
      worksheet.error = null;
      worksheet.columns = body.columns;
      worksheet.rows = body.rows;
      worksheet.truncated = body.truncated;
    } catch (err) {
      if (err.message === 'not authenticated') {
        // Redirect to /login is already in flight; don't flash an error.
        return;
      }
      worksheet.error = err.message;
      worksheet.columns = [];
      worksheet.rows = [];
      worksheet.truncated = false;
    }

    if (worksheet.id !== activeWorksheetId) return;
    renderResults(worksheet);
    loadHistory();
  } finally {
    queryInFlight = false;
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
    const body = await apiFetch('/catalog/namespaces');
    namespaces = body.namespaces;
  } catch (err) {
    if (err.message === 'not authenticated') return;
    tree.textContent = 'Failed to load catalog: ' + err.message;
    return;
  }

  tree.innerHTML = '';
  namespaces.forEach((ns) => {
    const nsRow = document.createElement('div');
    nsRow.className = 'tree-namespace-row';

    const nsEl = document.createElement('div');
    nsEl.className = 'tree-namespace';
    nsEl.textContent = '▸ ' + ns;
    nsEl.style.flex = '1';

    const nsActions = document.createElement('div');
    nsActions.className = 'tree-row-actions';
    const nsDetailsBtn = document.createElement('button');
    nsDetailsBtn.type = 'button';
    nsDetailsBtn.textContent = 'ⓘ';
    nsDetailsBtn.title = 'Details for ' + ns;
    nsDetailsBtn.setAttribute('aria-label', 'Details for ' + ns);
    nsDetailsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openNamespaceDetailsModal(ns);
    });
    const addTableBtn = document.createElement('button');
    addTableBtn.type = 'button';
    addTableBtn.textContent = '+';
    addTableBtn.title = 'New table in ' + ns;
    addTableBtn.setAttribute('aria-label', 'New table in ' + ns);
    addTableBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openCreateTableModal(ns);
    });
    const deleteNsBtn = document.createElement('button');
    deleteNsBtn.type = 'button';
    deleteNsBtn.textContent = '🗑';
    deleteNsBtn.title = 'Delete dataset ' + ns;
    deleteNsBtn.setAttribute('aria-label', 'Delete dataset ' + ns);
    deleteNsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      confirmDeleteDataset(ns);
    });
    nsActions.appendChild(nsDetailsBtn);
    nsActions.appendChild(addTableBtn);
    nsActions.appendChild(deleteNsBtn);

    nsRow.appendChild(nsEl);
    nsRow.appendChild(nsActions);

    const tablesEl = document.createElement('div');
    tablesEl.className = 'tree-tables';
    tablesEl.hidden = true;
    let loaded = false;

    nsRow.addEventListener('click', async () => {
      tablesEl.hidden = !tablesEl.hidden;
      nsEl.textContent = (tablesEl.hidden ? '▸ ' : '▾ ') + ns;
      if (!loaded) {
        loaded = true;
        try {
          const tablesBody = await apiFetch('/catalog/tables/' + encodeURIComponent(ns));
          tablesBody.tables.forEach((t) => {
            const tRow = document.createElement('div');
            tRow.className = 'tree-table-row';

            const tEl = document.createElement('div');
            tEl.className = 'tree-table';
            tEl.textContent = t;
            tEl.style.flex = '1';
            tEl.addEventListener('click', (e) => {
              e.stopPropagation();
              editor.replaceSelection('lakehouse.' + ns + '.' + t);
              editor.focus();
            });

            const tActions = document.createElement('div');
            tActions.className = 'tree-row-actions';
            const detailsBtn = document.createElement('button');
            detailsBtn.type = 'button';
            detailsBtn.textContent = 'ⓘ';
            detailsBtn.title = 'Details for ' + t;
            detailsBtn.setAttribute('aria-label', 'Details for ' + t);
            detailsBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              openTableDetailsModal(ns, t);
            });
            const deleteTBtn = document.createElement('button');
            deleteTBtn.type = 'button';
            deleteTBtn.textContent = '🗑';
            deleteTBtn.title = 'Delete table ' + t;
            deleteTBtn.setAttribute('aria-label', 'Delete table ' + t);
            deleteTBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              confirmDeleteTable(ns, t);
            });
            tActions.appendChild(detailsBtn);
            tActions.appendChild(deleteTBtn);

            tRow.appendChild(tEl);
            tRow.appendChild(tActions);
            tablesEl.appendChild(tRow);
          });
        } catch (err) {
          loaded = false;
          if (err.message === 'not authenticated') return;
          tablesEl.textContent = 'Failed to load tables: ' + err.message;
        }
      }
    });

    tree.appendChild(nsRow);
    tree.appendChild(tablesEl);
  });
}

// --- History ------------------------------------------------------------

async function loadHistory() {
  const list = document.getElementById('history-list');
  let history;
  try {
    const body = await apiFetch('/history');
    history = body.history;
  } catch (err) {
    if (err.message === 'not authenticated') return;
    list.textContent = 'Failed to load history: ' + err.message;
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

async function loadAccess() {
  const panel = document.getElementById('access-panel');
  panel.textContent = 'Loading...';
  let principals;
  try {
    const body = await apiFetch('/access');
    principals = body.principals;
  } catch (err) {
    if (err.message === 'not authenticated') return;
    panel.textContent = 'Failed to load access: ' + err.message;
    return;
  }

  panel.innerHTML = '';
  principals.forEach((p) => {
    const pEl = document.createElement('div');
    pEl.className = 'access-principal';
    const pName = document.createElement('div');
    pName.className = 'access-principal-name';
    pName.textContent = p.name;
    pEl.appendChild(pName);

    p.principal_roles.forEach((pr) => {
      const prEl = document.createElement('div');
      prEl.className = 'access-principal-role';
      prEl.textContent = pr.name;
      pEl.appendChild(prEl);

      pr.catalog_roles.forEach((cr) => {
        const crEl = document.createElement('div');
        crEl.className = 'access-catalog-role';
        crEl.textContent = cr.name + ': ' + cr.grants.join(', ');
        pEl.appendChild(crEl);
      });
    });

    panel.appendChild(pEl);
  });
}

// --- Sidebar panel switching ----------------------------------------------

function showSidebarPanel(panel) {
  document.getElementById('catalog-tree').hidden = panel !== 'catalog';
  document.getElementById('history-list').hidden = panel !== 'history';
  document.getElementById('access-panel').hidden = panel !== 'access';
  document.getElementById('tab-catalog').classList.toggle('active', panel === 'catalog');
  document.getElementById('tab-history').classList.toggle('active', panel === 'history');
  document.getElementById('tab-access').classList.toggle('active', panel === 'access');
  if (panel === 'history') loadHistory();
  if (panel === 'access') loadAccess();
}

// --- Whoami / logout --------------------------------------------------

async function loadWhoami() {
  try {
    const body = await apiFetch('/me');
    document.getElementById('whoami-text').textContent = body.principal + ' · ' + body.roles.join(', ');
  } catch (err) {
    if (err.message === 'not authenticated') return;
    document.getElementById('whoami-text').textContent = '(failed to load)';
  }
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
  document.getElementById('save-as-table-btn').addEventListener('click', openSaveAsTableModal);
  document.getElementById('tab-catalog').addEventListener('click', () => showSidebarPanel('catalog'));
  document.getElementById('tab-history').addEventListener('click', () => showSidebarPanel('history'));
  document.getElementById('tab-access').addEventListener('click', () => showSidebarPanel('access'));
  document.getElementById('refresh-catalog').addEventListener('click', loadCatalog);
  document.getElementById('new-dataset-btn').addEventListener('click', openCreateDatasetModal);
  document.getElementById('logout-btn').addEventListener('click', logout);

  loadWhoami();
  loadCatalog();
}

init();
