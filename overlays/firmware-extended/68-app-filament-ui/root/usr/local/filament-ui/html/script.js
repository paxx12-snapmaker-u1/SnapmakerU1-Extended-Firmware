'use strict';

// Subscribe to all fields so we get state[] push updates too
const QUERY_OBJECTS = {
    filament_detect: null,
    print_task_config: null,
};

const FD_STATE_IDLE      = 0;
const FD_STATE_DETECTING = 1;

let ws = null;
let wsReady = false;
let requestId = 1;
const pendingRequests = new Map();
let channelsData = [];
let subscribed = false;
let initialized = false;
let refreshing = false;
let spoolmanActive = false;
let spoolmanUrl = null;
let spoolmanSpools = new Map();
let spoolPickerChannel = null;
let spoolRefreshTimer = null;
const SPOOL_REFRESH_ACTIVE_MS = 3000;
const SPOOL_REFRESH_IDLE_MS = 30000;
let spoolPickerSpools = [];
let spoolPickerCurrentId = null;

// Last known full status — merged incrementally from notify_status_update
let cachedStatus = {
    filament_detect: { info: [{}, {}, {}, {}], state: [0, 0, 0, 0] },
    print_task_config: {},
};

document.addEventListener('DOMContentLoaded', () => {
    if (initialized) return;
    initialized = true;
    initializeWebSocket();
    initializeEventListeners();
    initializeModals();
});

// ── WebSocket ─────────────────────────────────────────────────────────────

function initializeWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/websocket`);

    ws.onopen = () => {
        sendRPC('server.connection.identify', {
            client_name: 'filament-manager',
            version: '1.0.0',
            type: 'web',
            url: location.href,
        }).then(() => {
            wsReady = true;
            setConnectionStatus(true);
            loadInitialData();
        }).catch(() => {
            showStatus('Failed to connect to Moonraker', 'error');
        });
    };

    ws.onclose = () => {
        wsReady = false;
        subscribed = false;
        setConnectionStatus(false);
        showStatus('Disconnected — reconnecting…', 'error');
        setTimeout(initializeWebSocket, 2000);
    };

    ws.onerror = () => showStatus('WebSocket error', 'error');

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        // Push update from subscription
        if (msg.method === 'notify_status_update') {
            const update = msg.params[0];
            if (update.filament_detect) {
                Object.assign(cachedStatus.filament_detect, update.filament_detect);
            }
            if (update.print_task_config) {
                Object.assign(cachedStatus.print_task_config, update.print_task_config);
            }
            rebuildFromCache();
            return;
        }

        // RPC response
        if (msg.id && pendingRequests.has(msg.id)) {
            const { resolve, reject } = pendingRequests.get(msg.id);
            pendingRequests.delete(msg.id);
            if (msg.error) reject(msg.error); else resolve(msg.result);
        }
    };
}

function sendRPC(method, params = {}) {
    return new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reject(new Error('WebSocket not connected'));
            return;
        }
        const id = requestId++;
        pendingRequests.set(id, { resolve, reject });
        ws.send(JSON.stringify({ jsonrpc: '2.0', method, params, id }));
        setTimeout(() => {
            if (pendingRequests.has(id)) {
                pendingRequests.delete(id);
                reject(new Error('Request timeout'));
            }
        }, 30000);
    });
}

async function sendGcode(gcode) {
    try {
        return await sendRPC('printer.gcode.script', { script: gcode });
    } catch (error) {
        if (error.message && error.message.includes('!!')) {
            const m = error.message.match(/!!\s*(.+)/);
            if (m) throw new Error(m[1]);
        }
        throw error;
    }
}

async function queryAndSubscribe() {
    if (!subscribed) {
        await sendRPC('printer.objects.subscribe', { objects: QUERY_OBJECTS });
        subscribed = true;
    }
    const result = await sendRPC('printer.objects.query', { objects: QUERY_OBJECTS });
    return result.status;
}

// ── Connection status ─────────────────────────────────────────────────────

function setConnectionStatus(connected) {
    const dot = document.getElementById('connection-status');
    const text = document.getElementById('connection-text');
    if (dot) dot.className = 'status-dot ' + (connected ? 'connected' : 'disconnected');
    if (text) text.textContent = connected ? 'Connected' : 'Disconnected';
    const btn = document.getElementById('refresh-all');
    if (btn && !refreshing) btn.disabled = !connected;
}

// ── Initial data load (no gcodes — shows existing printer state) ──────────

function loadSpoolmanStatus() {
    sendRPC('server.spoolman.status').then(status => {
        if (status.spoolman_connected) {
            spoolmanActive = true;
            if (!spoolRefreshTimer) scheduleSpoolRefresh();
        }
    }).catch(() => {});

    sendRPC('server.config').then(config => {
        spoolmanUrl = config.config?.spoolman?.server || null;
    }).catch(() => {});
}

async function loadInitialData() {
    try {
        const [status] = await Promise.all([queryAndSubscribe(), loadSpoolmanStatus()]);
        mergeStatus(status);
        rebuildFromCache();
        if (spoolmanActive) await refreshSpoolWeights();
    } catch (err) {
        showStatus(`Load failed: ${err.message}`, 'error');
    }
}

async function fetchSpoolmanAllSpools() {
    try {
        const result = await sendRPC('server.spoolman.proxy', { request_method: 'GET', path: '/v1/spool' });
        return Array.isArray(result) ? result : [];
    } catch { return []; }
}

async function fetchSpoolmanSpool(id) {
    try {
        return await sendRPC('server.spoolman.proxy', { request_method: 'GET', path: `/v1/spool/${id}` });
    } catch { return null; }
}

async function refreshSpoolWeights() {
    if (!spoolmanActive || !wsReady) return;
    const activeIds = [...new Set(channelsData.map(ch => ch.spool_id).filter(id => id != null))];
    if (activeIds.length === 0) return;
    await Promise.all(activeIds.map(async id => {
        const spool = await fetchSpoolmanSpool(id);
        if (spool) spoolmanSpools.set(id, spool);
    }));
    rebuildFromCache();
}

function scheduleSpoolRefresh() {
    const hasActiveSpool = channelsData.some(ch => ch.spool_id != null);
    const delay = hasActiveSpool ? SPOOL_REFRESH_ACTIVE_MS : SPOOL_REFRESH_IDLE_MS;
    spoolRefreshTimer = setTimeout(async () => {
        await refreshSpoolWeights();
        scheduleSpoolRefresh();
    }, delay);
}

function mergeStatus(status) {
    if (status.filament_detect)   Object.assign(cachedStatus.filament_detect,   status.filament_detect);
    if (status.print_task_config) Object.assign(cachedStatus.print_task_config, status.print_task_config);
}

function rebuildFromCache() {
    const fd  = cachedStatus.filament_detect;
    const ptc = cachedStatus.print_task_config;
    const detectInfo = fd.info || [{}, {}, {}, {}];
    const fdState    = fd.state || [0, 0, 0, 0];

    channelsData = [];
    for (let i = 0; i < 4; i++) {
        channelsData.push(parseChannelInfo(i, detectInfo[i] || {}, fdState[i] || 0, ptc));
    }
    renderChannels();
}

// ── Manual refresh (runs FILAMENT_DT gcodes) ──────────────────────────────

async function refreshAllChannels() {
    if (refreshing) return;
    if (!wsReady) { showStatus('Waiting for connection…', 'info'); return; }

    refreshing = true;
    const btn = document.getElementById('refresh-all');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Reading…'; }

    try {
        const gcodes = [];
        for (let i = 0; i < 4; i++) gcodes.push(`FILAMENT_DT_CLEAR CHANNEL=${i}`);
        for (let i = 0; i < 4; i++) gcodes.push(`FILAMENT_DT_UPDATE CHANNEL=${i}`);
        await sendGcode(gcodes.join('\n'));
    } catch (err) {
        showStatus(`Refresh failed: ${err.message}`, 'error');
    } finally {
        refreshing = false;
        if (btn) { btn.disabled = !wsReady; btn.textContent = 'Read spool tags'; }
    }
}

async function refreshSingleChannel(channel) {
    if (!wsReady) { showStatus('Waiting for connection…', 'info'); return; }
    try {
        await sendGcode(`FILAMENT_DT_CLEAR CHANNEL=${channel}\nFILAMENT_DT_UPDATE CHANNEL=${channel}`);
    } catch (err) {
        showStatus(`Refresh failed: ${err.message}`, 'error');
    }
}

// ── Channel parsing ───────────────────────────────────────────────────────

function parseChannelInfo(i, fd, fdState, ptc) {
    // CARD_UID always from filament_detect
    const uid = fd.CARD_UID || [];
    const hasUid = uid.length > 0;
    const cardType = fd.CARD_TYPE || null;
    const tagStatus = fd.TAG_STATUS || null;
    const fdMainType = fd.MAIN_TYPE && fd.MAIN_TYPE !== 'NONE' ? fd.MAIN_TYPE : null;
    const present = fdState === FD_STATE_DETECTING || hasUid;

    // official flag and existence from print_task_config
    const isOfficial = !!(ptc.filament_official && ptc.filament_official[i]);
    const filamentExists = !!(ptc.filament_exist && ptc.filament_exist[i]);

    // Basic info from print_task_config
    const ptcType       = ptc.filament_type       && ptc.filament_type[i];
    const ptcVendor     = ptc.filament_vendor      && ptc.filament_vendor[i];
    const ptcSubType    = ptc.filament_sub_type    && ptc.filament_sub_type[i];
    const ptcColorMulti = ptc.filament_color_multi && ptc.filament_color_multi[i];
    const ptcColorRgba  = ptc.filament_color_rgba  && ptc.filament_color_rgba[i];
    const spoolId       = ptc.filament_spool_id?.[i] > 0
        ? ptc.filament_spool_id[i] : null;

    const type    = ptcType    && ptcType    !== 'NONE' ? ptcType    : null;
    const vendor  = ptcVendor  && ptcVendor  !== 'NONE' ? ptcVendor  : null;
    const subtype = (ptcSubType == null || ptcSubType === 'NONE') ? null : ptcSubType;

    // Color from print_task_config
    let firstColor = null, alpha = 0xFF, additionalColors = [];
    if (ptcColorMulti && ptcColorMulti.nums > 0 && ptcColorMulti.colors) {
        const cols = ptcColorMulti.colors.slice(0, ptcColorMulti.nums);
        if (cols[0]) firstColor = cols[0].toUpperCase();
        alpha = ptcColorMulti.alpha !== undefined ? ptcColorMulti.alpha : 0xFF;
        additionalColors = cols.slice(1).map(c => c ? c.toUpperCase() : null).filter(Boolean);
    } else if (ptcColorRgba && ptcColorRgba.length >= 6) {
        firstColor = ptcColorRgba.substring(0, 6).toUpperCase();
        alpha = ptcColorRgba.length >= 8 ? parseInt(ptcColorRgba.substring(6, 8), 16) : 0xFF;
    }

    const spoolInfo = spoolId != null ? (spoolmanSpools.get(spoolId) ?? null) : null;
    const remainingWeight = spoolInfo != null && spoolInfo.remaining_weight != null
        ? Math.round(spoolInfo.remaining_weight) : null;

    // A bare UID card (third-party / unprogrammed) carries no filament
    // metadata, so the tag reports sentinel defaults — RGB_1 reads back as
    // FFFFFF (white) and the temps/diameter/density come back as zeros. Only
    // the Card UID is trustworthy in that case, so suppress the derived fields
    // unless the tag actually carries filament data (a material type or vendor).
    const rfidVendor  = fd.VENDOR && fd.VENDOR !== 'NONE' ? fd.VENDOR : null;
    const rfidHasData = !!(fdMainType || rfidVendor);
    const rfidData = hasUid ? {
        type:     fdMainType,
        sub_type: fd.SUB_TYPE && fd.SUB_TYPE !== 'NONE' && fd.SUB_TYPE !== 'Basic' ? fd.SUB_TYPE : null,
        vendor:   rfidVendor,
        color:    rfidHasData && fd.RGB_1 != null ? (fd.RGB_1 & 0xFFFFFF).toString(16).padStart(6, '0').toUpperCase() : null,
        min_temp: rfidHasData ? (fd.HOTEND_MIN_TEMP || null) : null,
        max_temp: rfidHasData ? (fd.HOTEND_MAX_TEMP || null) : null,
        bed_temp: rfidHasData ? (fd.BED_TEMP        || null) : null,
        diameter: rfidHasData && fd.DIAMETER ? fd.DIAMETER / 100.0 : null,
        density:  rfidHasData ? (fd.DENSITY         || null) : null,
    } : null;

    const cmpStr = (a, b) => !!(a && b && a.toLowerCase() !== b.toLowerCase());

    let mismatch = false;
    if (rfidData && spoolInfo) {
        const sm = spoolInfo.filament || {};
        const smColor = sm.color_hex ? sm.color_hex.replace('#', '').toUpperCase() : null;
        if (cmpStr(rfidData.type,   sm.material))     mismatch = true;
        if (cmpStr(rfidData.vendor, sm.vendor?.name)) mismatch = true;
        if (rfidData.color && smColor && rfidData.color !== smColor) mismatch = true;
    } else if (rfidData) {
        if (cmpStr(rfidData.type,     type))    mismatch = true;
        if (cmpStr(rfidData.vendor,   vendor))  mismatch = true;
        if (cmpStr(rfidData.sub_type, subtype)) mismatch = true;
        if (rfidData.color && firstColor && rfidData.color !== firstColor) mismatch = true;
    }

    const ptcSources = [];
    if (isOfficial) ptcSources.push('official');
    if (spoolId > 0) ptcSources.push('spoolman');
    if (ptcSources.length === 0) {
        if (type)                ptcSources.push('user');
        else if (filamentExists) ptcSources.push('unknown');
    }

    return {
        channel: i,
        present,
        filament_exists: filamentExists,
        official: isOfficial,
        uid,
        card_type: cardType,
        spool_id: spoolId,
        rfid_data: rfidData,
        mismatch,
        ptc_sources: ptcSources,
        empty: hasUid && !fdMainType && tagStatus !== 'error',
        malformed: hasUid && !fdMainType && tagStatus === 'error',
        filament: {
            type,
            brand: vendor,
            subtype,
            first_color: firstColor,
            alpha,
            additional_colors: additionalColors,
            remaining_weight: remainingWeight,
            // Extended fields only available via RFID
            diameter:     isOfficial && fd.DIAMETER         ? fd.DIAMETER / 100.0     : null,
            density:      isOfficial                        ? fd.DENSITY    || null   : null,
            min_temp:     isOfficial                        ? fd.HOTEND_MIN_TEMP || null : null,
            max_temp:     isOfficial                        ? fd.HOTEND_MAX_TEMP || null : null,
            bed_min_temp: isOfficial                        ? fd.BED_MIN_TEMP    || null : null,
            bed_max_temp: isOfficial                        ? fd.BED_MAX_TEMP    || null : null,
            weight:       isOfficial                        ? fd.WEIGHT     || null   : null,
        },
    };
}

// ── Rendering ─────────────────────────────────────────────────────────────

function renderChannels() {
    const grid = document.getElementById('channels-grid');
    grid.innerHTML = '';
    channelsData.forEach(ch => grid.appendChild(createChannelCard(ch)));
}

function createChannelCard(channel) {
    const card = document.createElement('div');
    card.className = 'channel-card';
    card.dataset.channel = channel.channel;

    const displayCh = channel.channel + 1;
    const { present, filament_exists: filamentExists, official: isOfficial,
            empty: isEmpty, malformed: isMalformed, filament, card_type, uid,
            spool_id: spoolId, mismatch, ptc_sources: ptcSources } = channel;
    const hasUid = uid.length > 0;
    const hasPtcInfo = !!filament.type;
    const showBody = present || filamentExists || hasPtcInfo || spoolmanActive;

    // ── Header ──
    const header = document.createElement('div');
    header.className = 'channel-header';

    const titleEl = document.createElement('span');
    titleEl.className = 'channel-title';
    titleEl.textContent = `Extruder ${displayCh}`;
    header.appendChild(titleEl);

    const badgesDiv = document.createElement('div');
    badgesDiv.className = 'header-badges';

    // Slot 1: [Empty] when nothing else to show
    if (ptcSources.length === 0 && !hasUid && !present) {
        const b = document.createElement('span');
        b.className = 'tag-type-badge';
        b.textContent = 'Empty';
        badgesDiv.appendChild(b);
    }

    // Slot 2: config source — Official / Spoolman / User / Unknown
    for (const src of ptcSources) {
        const b = document.createElement('span');
        if (src === 'official') {
            b.className = 'tag-type-badge official';
            b.textContent = 'Official';
        } else if (src === 'spoolman') {
            b.className = 'tag-type-badge spoolman';
            b.textContent = 'Spoolman';
            b.addEventListener('mouseenter', () => {
                const spool = spoolId != null ? spoolmanSpools.get(spoolId) : null;
                if (!spool) return;
                const rows = [{ label: 'ID', value: `#${spool.id}` }];
                if (spool.filament.name)            rows.push({ label: 'Name',      value: spool.filament.name });
                if (spool.filament.vendor?.name)    rows.push({ label: 'Brand',     value: spool.filament.vendor.name });
                if (spool.filament.material)        rows.push({ label: 'Material',  value: spool.filament.material });
                const smHexes = spool.filament.multi_color_hexes
                    ? spool.filament.multi_color_hexes.split(',').map(c => c.trim()).filter(Boolean)
                    : (spool.filament.color_hex ? [spool.filament.color_hex] : null);
                if (smHexes && smHexes.length > 0) {
                    const html = smHexes.map(c =>
                        `<span class="color-swatch" style="background:#${escHtml(c)}" title="#${escHtml(c)}"></span><span>#${escHtml(c)}</span>`
                    ).join(' ');
                    rows.push({ label: 'Color', html });
                }
                if (spool.remaining_weight != null) rows.push({ label: 'Remaining', value: `${Math.round(spool.remaining_weight)} g` });
                if (spool.filament.weight  != null) rows.push({ label: 'Total',     value: `${spool.filament.weight} g` });
                showPopover(b, rows);
            });
            b.addEventListener('mouseleave', hidePopover);
        } else if (src === 'unknown') {
            b.className = 'tag-type-badge unknown';
            b.textContent = 'Unknown';
        } else {
            b.className = 'tag-type-badge user';
            b.textContent = 'User';
        }
        badgesDiv.appendChild(b);
    }

    // Slot 3: physical presence — RFID (tag read) or Detecting (in progress)
    if (hasUid) {
        const b = document.createElement('span');
        b.className = 'tag-type-badge rfid';
        b.textContent = 'RFID';
        if (channel.rfid_data) {
            b.addEventListener('mouseenter', () => {
                const rfid = channel.rfid_data;
                const rows = [];
                if (rfid.type     != null) rows.push({ label: 'Type',    value: rfid.type });
                if (rfid.sub_type != null) rows.push({ label: 'Subtype', value: rfid.sub_type });
                if (rfid.vendor   != null) rows.push({ label: 'Vendor',  value: rfid.vendor });
                if (rfid.color    != null) rows.push({ label: 'Color',   value: `#${rfid.color}` });
                if (rfid.bed_temp != null) rows.push({ label: 'Bed',     value: `${rfid.bed_temp} °C` });
                if (rfid.min_temp != null) rows.push({ label: 'Hotend',  value: `${rfid.min_temp}–${rfid.max_temp ?? '?'} °C` });
                rows.push({ label: 'UID', value: uid.map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(':') });
                showPopover(b, rows);
            });
            b.addEventListener('mouseleave', hidePopover);
        }
        badgesDiv.appendChild(b);
    } else if (present) {
        const b = document.createElement('span');
        b.className = 'tag-type-badge detecting';
        b.textContent = 'Detecting';
        badgesDiv.appendChild(b);
    }

    const mBadge = document.createElement('span');
    mBadge.className = 'tag-type-badge' + (mismatch ? ' mismatch' : '');
    mBadge.textContent = '⚠ Mismatch';
    mBadge.style.display = mismatch ? '' : 'none';
    mBadge.addEventListener('click', e => { e.stopPropagation(); openMismatchModal(channel.channel); });
    badgesDiv.appendChild(mBadge);

    header.appendChild(badgesDiv);
    card.appendChild(header);

    // ── Body ──
    const body = document.createElement('div');
    body.className = 'channel-body';

    if (!showBody) {
        const empty = document.createElement('div');
        empty.className = 'channel-empty';
        empty.textContent = 'No spool detected';
        body.appendChild(empty);
    } else {
        const grid = document.createElement('div');
        grid.className = 'field-grid';

        const addRow = (label, html) => {
            const lEl = document.createElement('span');
            lEl.className = 'field-label';
            lEl.textContent = label;
            const vEl = document.createElement('span');
            vEl.className = 'field-value';
            vEl.innerHTML = html;
            grid.appendChild(lEl);
            grid.appendChild(vEl);
        };

        const na = '<span class="field-unknown">N/A</span>';

        if (filament.type) {
            const subtypePart = filament.subtype ? ` ${filament.subtype}` : '';
            const profileName = `${filament.brand || 'Generic'} ${filament.type}${subtypePart}`.trim();
            addRow('Material',
                `<span class="orca-profile-name">${escHtml(profileName)}</span>` +
                `<button type="button" class="copy-name-btn" aria-label="Copy name">⧉</button>`);
            grid.lastElementChild.querySelector('.copy-name-btn').addEventListener('click', () => {
                copyToClipboard(profileName)
                    .then(() => showStatus('Filament name copied', 'success'))
                    .catch(() => showStatus('Failed to copy filament name', 'error'));
            });
        } else {
            addRow('Material', na);
        }

        if (spoolmanActive) {
            let spoolIdHtml = na;
            if (spoolId != null) {
                spoolIdHtml = `#${spoolId}`;
                if (spoolmanUrl) spoolIdHtml += ` <a href="${spoolmanUrl}/spool/show/${spoolId}" target="_blank" rel="noopener">↗</a>`;
            }
            addRow('Spool ID', spoolIdHtml);
            addRow('Remaining', filament.remaining_weight != null
                ? `<span class="spool-remaining">${filament.remaining_weight} g</span>`
                : na);
        }

        if (isMalformed) {
            addRow('Status', '<span class="tag-warning">Unrecognized RFID data</span>');
        }

        if (filament.first_color) {
            const alphaStr = filament.alpha < 0xFF ? ` ${(filament.alpha / 255 * 100).toFixed(0)}%` : '';
            const colorHtml = [filament.first_color, ...filament.additional_colors].map(h =>
                `<span class="color-swatch" style="background:#${h}" title="#${h}"></span><span>#${h}</span>`
            ).join(' ') + alphaStr;
            addRow('Color', colorHtml);
        } else {
            addRow('Color', na);
        }

        const uidHex = hasUid
            ? uid.map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(':')
            : null;
        const uidDisplay = uidHex
            ? `<span class="uid-value">${uidHex}</span>${card_type ? ` (${card_type})` : ''}`
            : na;
        addRow('Card UID', uidDisplay);

        body.appendChild(grid);
    }
    card.appendChild(body);

    // ── Actions ──
    const actions = document.createElement('div');
    actions.className = 'channel-actions';

    const mkBtn = (text, extra, handler) => {
        const btn = document.createElement('button');
        btn.className = 'channel-action-btn' + (extra ? ' ' + extra : '');
        btn.textContent = text;
        btn.addEventListener('click', handler);
        return btn;
    };

    actions.appendChild(mkBtn('↻ Refresh', '', () => refreshSingleChannel(channel.channel)));
    actions.appendChild(mkBtn('✎ User', '', () => openOverwriteModal(channel.channel)));
    actions.appendChild(mkBtn('Reset', '', () => resetChannel(channel.channel)));
    if (spoolmanActive) actions.appendChild(mkBtn('⊕ Spool', '', () => openSpoolPicker(channel.channel)));

    card.appendChild(actions);
    return card;
}

// ── Info popover ──────────────────────────────────────────────────────────

function showPopover(anchorEl, rows) {
    const popover = document.getElementById('info-popover');
    popover.innerHTML = rows.map(r =>
        `<div class="popover-row">` +
        `<span class="popover-label">${escHtml(r.label)}</span>` +
        `<span class="popover-value">${r.html !== undefined ? r.html : escHtml(r.value)}</span>` +
        `</div>`
    ).join('');

    popover.style.visibility = 'hidden';
    popover.style.display = 'block';

    const rect = anchorEl.getBoundingClientRect();
    const pw = popover.offsetWidth;
    const ph = popover.offsetHeight;
    let top  = rect.bottom + 6;
    let left = rect.left;

    if (left + pw > window.innerWidth - 8)  left = window.innerWidth  - pw - 8;
    if (top  + ph > window.innerHeight - 8) top  = rect.top - ph - 6;

    popover.style.top  = `${top}px`;
    popover.style.left = `${left}px`;
    popover.style.visibility = '';
}

function hidePopover() {
    document.getElementById('info-popover').style.display = 'none';
}

// ── Spoolman picker ───────────────────────────────────────────────────────

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// navigator.clipboard requires a secure context, which this UI may not have
// when served over plain HTTP on the local network — fall back to the
// legacy execCommand approach in that case.
function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    }
    return new Promise((resolve, reject) => {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy') ? resolve() : reject(new Error('execCommand failed'));
        } catch (err) {
            reject(err);
        } finally {
            textarea.remove();
        }
    });
}

async function openSpoolPicker(channel) {
    spoolPickerChannel = channel;
    document.getElementById('spoolman-modal-title').textContent = `Pick Spool — Extruder ${channel + 1}`;
    document.getElementById('spoolman-search').value = '';
    const list = document.getElementById('spoolman-list');
    list.innerHTML = '<div class="spoolman-loading"><span class="spinner"></span> Loading…</div>';
    openModal('spoolman-modal');

    const spools = await fetchSpoolmanAllSpools();
    spoolPickerSpools = spools;
    const ch = channelsData.find(c => c.channel === channel);
    spoolPickerCurrentId = ch ? ch.spool_id : null;
    renderSpoolList('');
}

function renderSpoolList(filter) {
    const list = document.getElementById('spoolman-list');
    const lower = filter.toLowerCase();
    const filtered = spoolPickerSpools.filter(s => {
        if (s.is_archived) return false;
        if (!lower) return true;
        const name    = (s.filament.name     || '').toLowerCase();
        const material= (s.filament.material || '').toLowerCase();
        const vendor  = (s.filament.vendor?.name || '').toLowerCase();
        return name.includes(lower) || material.includes(lower)
            || vendor.includes(lower) || String(s.id).includes(lower);
    });

    if (filtered.length === 0) {
        list.innerHTML = '<div class="spoolman-empty">No spools found</div>';
        return;
    }

    list.innerHTML = '';
    filtered.forEach(s => {
        const item = document.createElement('div');
        item.className = 'spoolman-item' + (s.id === spoolPickerCurrentId ? ' active' : '');
        const name     = s.filament.name || s.filament.material || 'Unknown';
        const vendor   = s.filament.vendor?.name || '';
        const material = s.filament.material || '';
        const meta     = [vendor, material].filter(Boolean).join(' · ');
        const weight   = s.remaining_weight != null ? `${Math.round(s.remaining_weight)} g` : '—';
        const multiHexes = s.filament.multi_color_hexes
            ? s.filament.multi_color_hexes.split(',').map(c => c.trim()).filter(Boolean)
            : null;
        const swatchColors = (multiHexes && multiHexes.length > 0) ? multiHexes : [s.filament.color_hex || 'CCCCCC'];
        const swatchesHtml = swatchColors
            .map(c => `<div class="spoolman-swatch" style="background:#${escHtml(c)}"></div>`)
            .join('');
        item.innerHTML =
            `<div class="spoolman-swatches">${swatchesHtml}</div>` +
            `<div class="spoolman-info">` +
                `<div class="spoolman-name">${escHtml(name)}</div>` +
                `<div class="spoolman-meta">${escHtml(meta)}</div>` +
            `</div>` +
            `<div class="spoolman-right">` +
                `<div class="spoolman-weight">${escHtml(weight)}</div>` +
                `<div class="spoolman-id">#${s.id}</div>` +
            `</div>`;
        item.addEventListener('click', () => pickSpool(s.id));
        list.appendChild(item);
    });
}

async function pickSpool(spoolId) {
    if (spoolPickerChannel === null) return;
    const channel = spoolPickerChannel;
    closeModal('spoolman-modal');
    try {
        showStatus('Assigning spool…', 'info');
        await sendGcode(`SET_SPOOL_ID LANE=E${channel} SPOOL_ID=${spoolId}`);
        const spool = await fetchSpoolmanSpool(spoolId);
        if (spool) spoolmanSpools.set(spoolId, spool);
        showStatus(`Spool #${spoolId} assigned to Extruder ${channel + 1}`, 'success');
    } catch (err) {
        showStatus(`Failed to assign spool: ${err.message}`, 'error');
    }
}

async function clearSpoolForChannel(channel) {
    closeModal('spoolman-modal');
    try {
        showStatus('Clearing spool…', 'info');
        await sendGcode(`SET_SPOOL_ID LANE=E${channel} SPOOL_ID=0`);
        showStatus(`Extruder ${channel + 1} spool cleared`, 'success');
    } catch (err) {
        showStatus(`Failed to clear spool: ${err.message}`, 'error');
    }
}

// ── Mismatch modal ────────────────────────────────────────────────────────

function openMismatchModal(channel) {
    const ch = channelsData.find(c => c.channel === channel);
    if (!ch) return;

    document.getElementById('mismatch-modal-title').textContent =
        `Tag Mismatch — Extruder ${channel + 1}`;
    document.getElementById('mismatch-apply').dataset.channel = channel;

    const rfid      = ch.rfid_data || {};
    const f         = ch.filament;
    const spoolInfo = ch.spool_id != null ? spoolmanSpools.get(ch.spool_id) : null;
    const sm        = spoolInfo?.filament || null;
    const smColor   = sm?.color_hex ? sm.color_hex.replace('#', '').toUpperCase() : null;

    const otherLabel   = sm ? 'Spoolman' : 'Printer Config';
    const otherType    = sm ? sm.material          : f.type;
    const otherVendor  = sm ? sm.vendor?.name      : f.brand;
    const otherSubtype = sm ? null                 : f.subtype;
    const otherColor   = sm ? smColor              : f.first_color;

    const cmpStr = (a, b) => !!(a && b && a.toLowerCase() !== b.toLowerCase());
    const cmpHex = (a, b) => !!(a && b && a !== b);
    const hexVal = h => h ? `#${h}` : '—';

    const rows = [];
    if (rfid.type     || otherType)    rows.push({ label: 'Type',    rfid: rfid.type     || '—', other: otherType    || '—', differs: cmpStr(rfid.type,     otherType) });
    if (rfid.vendor   || otherVendor)  rows.push({ label: 'Vendor',  rfid: rfid.vendor   || '—', other: otherVendor  || '—', differs: cmpStr(rfid.vendor,   otherVendor) });
    if (rfid.sub_type || otherSubtype) rows.push({ label: 'Subtype', rfid: rfid.sub_type || '—', other: otherSubtype || '—', differs: cmpStr(rfid.sub_type, otherSubtype) });
    if (rfid.color    || otherColor)   rows.push({ label: 'Color',   rfid: hexVal(rfid.color),   other: hexVal(otherColor),  differs: cmpHex(rfid.color, otherColor) });
    if (rfid.bed_temp != null) rows.push({ label: 'Bed',    rfid: `${rfid.bed_temp} °C`,                          other: '—', differs: false });
    if (rfid.min_temp != null) rows.push({ label: 'Hotend', rfid: `${rfid.min_temp}–${rfid.max_temp ?? '?'} °C`, other: '—', differs: false });

    const content = document.getElementById('mismatch-content');
    content.innerHTML = '';

    const hdr = document.createElement('div');
    hdr.className = 'mismatch-row mismatch-header';
    hdr.innerHTML = `<span></span><span>RFID Tag</span><span>${escHtml(otherLabel)}</span>`;
    content.appendChild(hdr);

    rows.forEach(r => {
        const row = document.createElement('div');
        row.className = 'mismatch-row' + (r.differs ? ' mismatch-differs' : '');
        row.innerHTML =
            `<span class="mismatch-label">${escHtml(r.label)}</span>` +
            `<span class="mismatch-val">${escHtml(r.rfid)}</span>` +
            `<span class="mismatch-val">${escHtml(r.other)}</span>`;
        content.appendChild(row);
    });

    openModal('mismatch-modal');
}

// ── Modals ────────────────────────────────────────────────────────────────

function openModal(id) {
    const el = document.getElementById(id);
    if (el) { el.style.display = ''; document.body.classList.add('modal-open'); }
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
    if (!document.querySelector('.tag-edit-overlay:not([style*="none"])')) {
        document.body.classList.remove('modal-open');
    }
}

function initializeModals() {
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            closeModal('overwrite-modal'); closeModal('spoolman-modal');
            closeModal('mismatch-modal');
        }
    });

    document.getElementById('mismatch-close').addEventListener('click', () => closeModal('mismatch-modal'));
    document.getElementById('mismatch-cancel').addEventListener('click', () => closeModal('mismatch-modal'));
    document.getElementById('mismatch-modal').addEventListener('click', e => {
        if (e.target.id === 'mismatch-modal') closeModal('mismatch-modal');
    });
    document.getElementById('mismatch-apply').addEventListener('click', async () => {
        const channel = parseInt(document.getElementById('mismatch-apply').dataset.channel);
        const ch = channelsData.find(c => c.channel === channel);
        const rfid = ch?.rfid_data;
        closeModal('mismatch-modal');
        if (!rfid || !rfid.type) {
            showStatus('No RFID filament data to apply', 'error');
            return;
        }
        // Push the tag's data straight into print_task_config rather than
        // re-reading the tag (FILAMENT_DT_UPDATE), which would fan out to a
        // Spoolman card-UID resolve and possibly overwrite the tag data.
        const colors = rfid.color ? [rfid.color] : [];
        const gcode = [
            'SET_PRINT_FILAMENT_CONFIG',
            `CONFIG_EXTRUDER=${channel}`,
            `VENDOR="${rfid.vendor || 'Generic'}"`,
            `FILAMENT_TYPE=${rfid.type}`,
            `FILAMENT_SUBTYPE="${rfid.sub_type || ''}"`,
            `COLOR_NUMS=${colors.length}`,
            `COLORS=${colors.join(',')}`,
            'MULTI_MODE=0',
            'ALPHA=255',
            'FORCE=1',
        ].join(' ');
        try {
            showStatus('Applying RFID data…', 'info');
            await sendGcode(gcode);
            showStatus('RFID data applied', 'success');
        } catch (err) {
            showStatus(`Failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('spoolman-close').addEventListener('click', () => closeModal('spoolman-modal'));
    document.getElementById('spoolman-cancel').addEventListener('click', () => closeModal('spoolman-modal'));
    document.getElementById('spoolman-clear').addEventListener('click', () => {
        if (spoolPickerChannel !== null) clearSpoolForChannel(spoolPickerChannel);
    });
    document.getElementById('spoolman-modal').addEventListener('click', e => {
        if (e.target.id === 'spoolman-modal') closeModal('spoolman-modal');
    });
    document.getElementById('spoolman-search').addEventListener('input', e => {
        renderSpoolList(e.target.value);
    });

    document.querySelectorAll('.modal-close-ow').forEach(btn => {
        btn.addEventListener('click', () => closeModal('overwrite-modal'));
    });
    document.getElementById('overwrite-modal').addEventListener('click', e => {
        if (e.target.id === 'overwrite-modal') closeModal('overwrite-modal');
    });

    document.getElementById('overwrite-form').addEventListener('submit', handleOverwriteFilament);

    document.getElementById('ow-color-opacity').addEventListener('input', updateAllColorPreviews);
    initColorSlots();

    initFilamentPalette('material');
    initFilamentPalette('brand');
    initFilamentPalette('subtype');
    document.getElementById('ow-brand-custom').addEventListener('input', e => {
        document.getElementById('ow-brand-value').value = e.target.value.trim();
    });
}

// ── Reset channel ─────────────────────────────────────────────────────────

async function resetChannel(channel) {
    if (!wsReady) return;
    try {
        showStatus(`Resetting extruder ${channel + 1}…`, 'info');
        await sendGcode(`FILAMENT_DT_CLEAR CHANNEL=${channel}`);
        showStatus(`Extruder ${channel + 1} reset`, 'success');
    } catch (err) {
        showStatus(`Reset failed: ${err.message}`, 'error');
    }
}

// ── User filament ─────────────────────────────────────────────────────────

const FILAMENT_MAX_COLORS = 3;

// Tap-to-select palettes (mirrors repos/PrintTag-Web's material-palette pattern)
const FILAMENT_PALETTES = {
    material: {
        paletteId: 'ow-material-palette',
        valueId: 'ow-material-value',
        items: ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PC', 'PVA', 'NYLON',
                'PLA-CF', 'PETG-CF', 'PA-CF', 'ABS-GF',
                'PA', 'PC-ABS', 'HIPS', 'BVOH'],
        defaultValue: 'PLA',
    },
    brand: {
        paletteId: 'ow-brand-palette',
        valueId: 'ow-brand-value',
        items: ['Generic', 'Bambu Lab', 'Hatchbox', 'eSun', 'Overture',
                'SUNLU', 'Polymaker', 'Prusament', 'Snapmaker', 'Jayo'],
        defaultValue: 'Generic',
        customInputId: 'ow-brand-custom',
    },
    subtype: {
        paletteId: 'ow-subtype-palette',
        valueId: 'ow-subtype-value',
        items: [{ label: 'None', value: '' }, 'Basic', 'Matte', 'SnapSpeed', 'Silk', 'Support', 'HF',
                '95A', '95A HF', '90A', '85A', 'Wood', 'Translucent'],
        defaultValue: 'Basic',
    },
};

function itemLabel(item) { return typeof item === 'object' ? item.label : item; }
function itemValue(item) { return typeof item === 'object' ? item.value : item; }

function createPaletteTile(label, value, onSelect) {
    const tile = document.createElement('div');
    tile.className = 'palette-tile';
    tile.textContent = label;
    tile.dataset.value = value;
    tile.setAttribute('role', 'button');
    tile.setAttribute('tabindex', '0');
    tile.addEventListener('click', onSelect);
    tile.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); }
    });
    return tile;
}

function initFilamentPalette(name) {
    const config = FILAMENT_PALETTES[name];
    const palette = document.getElementById(config.paletteId);
    palette.innerHTML = '';
    config.items.forEach(item => {
        const value = itemValue(item);
        palette.appendChild(createPaletteTile(itemLabel(item), value, () => setFilamentPaletteValue(name, value)));
    });
    if (config.customInputId) {
        palette.appendChild(createPaletteTile('Custom', 'custom', () => setFilamentPaletteValue(name, 'custom')));
    }
}

function setFilamentPaletteValue(name, value) {
    const config = FILAMENT_PALETTES[name];
    const valueInput = document.getElementById(config.valueId);
    const isCustom = !!config.customInputId && value === 'custom';

    if (config.customInputId) {
        const customInput = document.getElementById(config.customInputId);
        if (isCustom) {
            customInput.style.display = '';
            customInput.focus();
            valueInput.value = customInput.value.trim() || '';
        } else {
            customInput.style.display = 'none';
            valueInput.value = value;
        }
    } else {
        valueInput.value = value;
    }

    const selectedTileValue = isCustom ? 'Custom' : value;
    document.querySelectorAll(`#${config.paletteId} .palette-tile`).forEach(tile => {
        tile.classList.toggle('selected', tile.dataset.value === selectedTileValue);
    });
}

// Selects `value`, adding a one-off tile for it first if it isn't one of the
// known items (e.g. a subtype set previously via free text, or RFID data).
function setFilamentValue(name, value) {
    const config = FILAMENT_PALETTES[name];
    if (value == null) { setFilamentPaletteValue(name, config.defaultValue); return; }
    if (config.items.some(item => itemValue(item) === value)) { setFilamentPaletteValue(name, value); return; }
    if (!value) { setFilamentPaletteValue(name, config.defaultValue); return; }
    if (config.customInputId) {
        document.getElementById(config.customInputId).value = value;
        setFilamentPaletteValue(name, 'custom');
        return;
    }
    const palette = document.getElementById(config.paletteId);
    if (![...palette.children].some(t => t.dataset.value === value)) {
        palette.appendChild(createPaletteTile(value, value, () => setFilamentPaletteValue(name, value)));
    }
    setFilamentPaletteValue(name, value);
}

// ── HSV color picker (ported from repos/PrintTag-Web/public/color.js) ──────

function hsvToRgb(h, s, v) {
    let r, g, b;
    const i = Math.floor(h * 6);
    const f = h * 6 - i;
    const p = v * (1 - s);
    const q = v * (1 - f * s);
    const t = v * (1 - (1 - f) * s);
    switch (i % 6) {
        case 0: r = v; g = t; b = p; break;
        case 1: r = q; g = v; b = p; break;
        case 2: r = p; g = v; b = t; break;
        case 3: r = p; g = q; b = v; break;
        case 4: r = t; g = p; b = v; break;
        case 5: r = v; g = p; b = q; break;
    }
    return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h, s, v = max;
    const d = max - min;
    s = max === 0 ? 0 : d / max;
    if (max === min) {
        h = 0;
    } else {
        switch (max) {
            case r: h = (g - b) / d + (g < b ? 6 : 0); break;
            case g: h = (b - r) / d + 2; break;
            case b: h = (r - g) / d + 4; break;
        }
        h /= 6;
    }
    return [h, s, v];
}

function hexToHsv(hex) {
    const val = hex.replace('#', '').trim();
    if (!/^[0-9a-fA-F]{6}$/.test(val)) return null;
    return rgbToHsv(parseInt(val.slice(0, 2), 16), parseInt(val.slice(2, 4), 16), parseInt(val.slice(4, 6), 16));
}

function hsvToHex(h, s, v) {
    return hsvToRgb(h, s, v).map(c => c.toString(16).padStart(2, '0').toUpperCase()).join('');
}

function drawHueArea(ctx, w, h) {
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    for (let i = 0; i <= 6; i++) {
        const stop = i / 6;
        const [r, g, b] = hsvToRgb(stop, 1, 1);
        grad.addColorStop(stop, `rgb(${r},${g},${b})`);
    }
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
}

function drawSVSquare(ctx, w, h, hue) {
    const [r, g, b] = hsvToRgb(hue, 1, 1);
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(0, 0, w, h);
    const white = ctx.createLinearGradient(0, 0, w, 0);
    white.addColorStop(0, 'rgba(255,255,255,1)');
    white.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = white;
    ctx.fillRect(0, 0, w, h);
    const black = ctx.createLinearGradient(0, 0, 0, h);
    black.addColorStop(0, 'rgba(0,0,0,0)');
    black.addColorStop(1, 'rgba(0,0,0,1)');
    ctx.fillStyle = black;
    ctx.fillRect(0, 0, w, h);
}

function drawSVMarker(ctx, w, h, s, v) {
    const x = Math.max(0, Math.min(w, Math.round(s * w)));
    const y = Math.max(0, Math.min(h, Math.round((1 - v) * h)));
    const r = Math.max(4, Math.floor(Math.min(w, h) * 0.04));
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(255,255,255,0.95)';
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y, r - 2, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(0,0,0,0.65)';
    ctx.stroke();
    ctx.restore();
}

function drawHueMarker(ctx, w, h, hue) {
    const x = Math.max(0, Math.min(w, Math.round(hue * w)));
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(255,255,255,0.95)';
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + 3, 0);
    ctx.lineTo(x + 3, h);
    ctx.strokeStyle = 'rgba(0,0,0,0.5)';
    ctx.stroke();
    ctx.restore();
}

function sizeCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(canvas.clientWidth * dpr));
    canvas.height = Math.max(1, Math.floor(canvas.clientHeight * dpr));
}

// ── Color slots (up to FILAMENT_MAX_COLORS, pre-created and toggleable) ────

const colorSlotState = Array.from({ length: FILAMENT_MAX_COLORS }, () => ({ hue: 0, s: 0, v: 1, enabled: false }));
colorSlotState[0].enabled = true;

function redrawColorSlot(index) {
    const slot = document.querySelector(`.color-slot[data-index="${index}"]`);
    const st = colorSlotState[index];
    const sv = slot.querySelector('.color-sv');
    const hue = slot.querySelector('.color-hue');
    if (!sv || !hue) return;

    const svCtx = sv.getContext('2d');
    drawSVSquare(svCtx, sv.width, sv.height, st.hue);
    drawSVMarker(svCtx, sv.width, sv.height, st.s, st.v);

    const hueCtx = hue.getContext('2d');
    drawHueArea(hueCtx, hue.width, hue.height);
    drawHueMarker(hueCtx, hue.width, hue.height, st.hue);
}

function updateColorSlotPreview(index) {
    const st = colorSlotState[index];
    const slot = document.querySelector(`.color-slot[data-index="${index}"]`);
    const preview = slot && slot.querySelector('.color-preview');
    if (!preview) return;
    const percent = parseInt(document.getElementById('ow-color-opacity').value, 10) || 0;
    const [r, g, b] = hsvToRgb(st.hue, st.s, st.v);
    preview.style.setProperty('--preview-color', `rgba(${r}, ${g}, ${b}, ${percent / 100})`);
}

function updateAllColorPreviews() {
    const percent = parseInt(document.getElementById('ow-color-opacity').value, 10) || 0;
    const alphaHex = Math.round(percent / 100 * 255).toString(16).padStart(2, '0').toUpperCase();
    document.getElementById('ow-color-alpha').value = alphaHex;
    document.getElementById('ow-color-opacity-value').textContent = `${percent}%`;
    colorSlotState.forEach((st, i) => { if (st.enabled) updateColorSlotPreview(i); });
}

function applyColorSlotChange(index) {
    const st = colorSlotState[index];
    const slot = document.querySelector(`.color-slot[data-index="${index}"]`);
    const hexInput = slot.querySelector('.color-hex-input');
    hexInput.value = hsvToHex(st.hue, st.s, st.v);
    redrawColorSlot(index);
    updateColorSlotPreview(index);
}

function attachDrag(el, onPick) {
    const pick = (clientX, clientY) => {
        const rect = el.getBoundingClientRect();
        onPick((clientX - rect.left) / rect.width, (clientY - rect.top) / rect.height);
    };
    let dragging = false;
    el.addEventListener('mousedown', e => { dragging = true; pick(e.clientX, e.clientY); });
    el.addEventListener('mousemove', e => { if (dragging) pick(e.clientX, e.clientY); });
    window.addEventListener('mouseup', () => { dragging = false; });
    el.addEventListener('touchstart', e => { pick(e.touches[0].clientX, e.touches[0].clientY); }, { passive: true });
    el.addEventListener('touchmove', e => {
        pick(e.touches[0].clientX, e.touches[0].clientY);
        e.preventDefault();
    }, { passive: false });
}

function renderColorSlotContent(index) {
    const slot = document.querySelector(`.color-slot[data-index="${index}"]`);
    const st = colorSlotState[index];

    slot.innerHTML =
        `<div class="color-slot-header">` +
            `<input type="text" class="color-hex-input" maxlength="6" spellcheck="false">` +
            `<div class="color-preview" aria-hidden="true"></div>` +
            (index === 0 ? '' : `<button type="button" class="color-toggle-btn" aria-label="Remove color">&times;</button>`) +
        `</div>` +
        `<canvas class="color-sv"></canvas>` +
        `<canvas class="color-hue"></canvas>`;

    const hexInput = slot.querySelector('.color-hex-input');
    const svCanvas = slot.querySelector('.color-sv');
    const hueCanvas = slot.querySelector('.color-hue');

    hexInput.addEventListener('input', () => {
        const hsv = hexToHsv(hexInput.value);
        if (!hsv) return;
        [st.hue, st.s, st.v] = hsv;
        redrawColorSlot(index);
        updateColorSlotPreview(index);
    });

    attachDrag(svCanvas, (x, y) => {
        st.s = Math.max(0, Math.min(1, x));
        st.v = 1 - Math.max(0, Math.min(1, y));
        applyColorSlotChange(index);
    });
    attachDrag(hueCanvas, (x) => {
        st.hue = Math.max(0, Math.min(1, x));
        applyColorSlotChange(index);
    });

    if (index !== 0) {
        slot.querySelector('.color-toggle-btn').addEventListener('click', () => disableColorSlot(index));
    }

    sizeCanvas(svCanvas);
    sizeCanvas(hueCanvas);
    applyColorSlotChange(index);
}

// Renders exactly the currently-enabled slots, followed by a single
// "+ Add color" button (hidden once FILAMENT_MAX_COLORS is reached).
function renderColorList() {
    const list = document.getElementById('ow-color-list');
    list.innerHTML = '';

    let activeCount = 0;
    colorSlotState.forEach((st, i) => {
        if (!st.enabled) return;
        activeCount++;
        const slot = document.createElement('div');
        slot.className = 'color-slot';
        slot.dataset.index = i;
        list.appendChild(slot);
        renderColorSlotContent(i);
    });

    if (activeCount < FILAMENT_MAX_COLORS) {
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'color-slot-add-btn';
        addBtn.textContent = '+ Add color';
        addBtn.addEventListener('click', () => {
            const next = colorSlotState.findIndex(st => !st.enabled);
            if (next !== -1) enableColorSlot(next);
        });
        list.appendChild(addBtn);
    }
}

function enableColorSlot(index) {
    colorSlotState[index].enabled = true;
    colorSlotState[index].hue = 0;
    colorSlotState[index].s = 0;
    colorSlotState[index].v = 1;
    renderColorList();
}

function disableColorSlot(index) {
    colorSlotState[index].enabled = false;
    renderColorList();
}

function initColorSlots() {
    renderColorList();
}

function setColorSlots(hexes) {
    const list = hexes.length ? hexes : ['FFFFFF'];
    colorSlotState.forEach((st, i) => {
        st.enabled = i === 0 || i < list.length;
        const hsv = hexToHsv(list[i] || 'FFFFFF') || [0, 0, 1];
        [st.hue, st.s, st.v] = hsv;
    });
    renderColorList();
}

function resizeVisibleColorCanvases() {
    colorSlotState.forEach((st, i) => {
        if (!st.enabled) return;
        const slot = document.querySelector(`.color-slot[data-index="${i}"]`);
        sizeCanvas(slot.querySelector('.color-sv'));
        sizeCanvas(slot.querySelector('.color-hue'));
        redrawColorSlot(i);
    });
}

function openOverwriteModal(channel) {
    const form = document.getElementById('overwrite-form');
    document.getElementById('overwrite-modal-title').textContent = `User Filament — Extruder ${channel + 1}`;
    form.reset();
    form.elements.channel.value = channel;
    setFilamentValue('material', 'PLA');
    setFilamentValue('brand', 'Generic');
    setFilamentValue('subtype', 'Basic');
    document.getElementById('ow-color-opacity').value = 100;
    setColorSlots(['FFFFFF']);

    const ch = channelsData.find(c => c.channel === channel);
    if (ch && ch.filament && ch.filament.type) {
        setFilamentValue('material', ch.filament.type);
        if (ch.filament.brand)   setFilamentValue('brand', ch.filament.brand);
        if (ch.filament.subtype != null) setFilamentValue('subtype', ch.filament.subtype);
        if (ch.filament.first_color) {
            setColorSlots([ch.filament.first_color, ...(ch.filament.additional_colors || [])]);
            const alpha = ch.filament.alpha !== undefined ? ch.filament.alpha : 0xFF;
            document.getElementById('ow-color-opacity').value = Math.round(alpha / 255 * 100);
        }
    }

    const sources = [];
    if (ch && ch.official) sources.push('an RFID tag');
    if (ch && ch.spool_id != null) sources.push('Spoolman');
    const warningEl = document.getElementById('overwrite-warning');
    if (sources.length) {
        warningEl.textContent = `This extruder's filament is currently set from ${sources.join(' and ')}. Saving here will overwrite it.`;
        warningEl.style.display = '';
    } else {
        warningEl.style.display = 'none';
    }

    updateAllColorPreviews();
    openModal('overwrite-modal');
    resizeVisibleColorCanvases();
}

async function handleOverwriteFilament(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const channel  = formData.get('channel');
    const type     = formData.get('type');
    const brand    = (formData.get('brand')   || 'Generic').trim();
    const subtype  = (formData.get('subtype') ?? '').trim();
    const alphaHex = (formData.get('alpha') || 'FF').trim().toUpperCase();
    const alphaDec = parseInt(alphaHex, 16);

    const colors = colorSlotState
        .filter(st => st.enabled)
        .map(st => hsvToHex(st.hue, st.s, st.v));

    const gcode = [
        'SET_PRINT_FILAMENT_CONFIG',
        `CONFIG_EXTRUDER=${channel}`,
        `VENDOR="${brand}"`,
        `FILAMENT_TYPE=${type}`,
        `FILAMENT_SUBTYPE="${subtype}"`,
        `COLOR_NUMS=${colors.length}`,
        `COLORS=${colors.join(',')}`,
        'MULTI_MODE=0',
        `ALPHA=${alphaDec}`,
        'FORCE=1',
    ].join(' ');

    try {
        showStatus('Setting filament…', 'info');
        await sendGcode(gcode);
        closeModal('overwrite-modal');
        showStatus(`Extruder ${parseInt(channel) + 1} filament set`, 'success');
    } catch (err) {
        showStatus(`Failed: ${err.message}`, 'error');
    }
}

// ── Event listeners ───────────────────────────────────────────────────────

function initializeEventListeners() {
    const btn = document.getElementById('refresh-all');
    if (btn) btn.addEventListener('click', refreshAllChannels);
}

// ── Status toast ──────────────────────────────────────────────────────────

function showStatus(message, type = 'info') {
    const el = document.getElementById('status-message');
    if (!el) return;
    el.textContent = message;
    el.className = `status-message status-${type}`;
    el.classList.add('show');
    if (type !== 'error') setTimeout(() => el.classList.remove('show'), 5000);
}
