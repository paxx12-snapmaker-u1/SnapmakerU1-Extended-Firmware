// RFID Tag Manager JavaScript - Moonraker Websocket Version

// Material default temperatures (for reference)
const MATERIAL_DEFAULTS = {
    'PLA': { min_temp: 190, max_temp: 220, bed_min_temp: 50, bed_max_temp: 70, density: 1.24 },
    'PETG': { min_temp: 220, max_temp: 250, bed_min_temp: 70, bed_max_temp: 90, density: 1.27 },
    'ABS': { min_temp: 230, max_temp: 260, bed_min_temp: 90, bed_max_temp: 110, density: 1.04 },
    'TPU': { min_temp: 210, max_temp: 230, bed_min_temp: 40, bed_max_temp: 60, density: 1.21 },
    'PVA': { min_temp: 190, max_temp: 210, bed_min_temp: 50, bed_max_temp: 70, density: 1.19 },
    'NYLON': { min_temp: 240, max_temp: 270, bed_min_temp: 70, bed_max_temp: 90, density: 1.14 },
    'ASA': { min_temp: 240, max_temp: 260, bed_min_temp: 90, bed_max_temp: 110, density: 1.07 },
    'PC': { min_temp: 260, max_temp: 290, bed_min_temp: 100, bed_max_temp: 120, density: 1.20 }
};

// State
let ws = null;
let wsReady = false;
let requestId = 1;
let pendingRequests = new Map();
let channelsData = [];
let colorPickers = {};
let userModifiedColors = new Set();
let currentModalChannel = null;
let currentModalMode = null; // 'create' or 'update'
let subscribed = false; // Track if we've subscribed to filament_detect
let initialized = false; // Track if page has been initialized
let refreshing = false; // Track if refresh is in progress

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    if (initialized) {
        console.warn('Already initialized, skipping');
        return;
    }
    initialized = true;
    console.log('Initializing RFID Manager');

    initializeWebSocket();
    initializeColorPickers();
    initializeEventListeners();
    initializeModals();
});

// ============================================================================
// Moonraker Websocket Connection
// ============================================================================

function initializeWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/websocket`;

    console.log('Connecting to Moonraker websocket:', wsUrl);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('Websocket connected');
        // Identify client
        sendRPC('server.connection.identify', {
            client_name: 'rfid-manager',
            version: '1.0.0',
            type: 'web',
            url: window.location.href
        }).then(() => {
            wsReady = true;
            showStatus('Connected to Moonraker', 'success');
            refreshAllChannels();
        }).catch(err => {
            console.error('Failed to identify client:', err);
            showStatus('Failed to connect to Moonraker', 'error');
        });
    };

    ws.onclose = () => {
        console.log('Websocket disconnected');
        wsReady = false;
        showStatus('Disconnected from Moonraker. Reconnecting...', 'error');
        // Reconnect after 2 seconds
        setTimeout(() => initializeWebSocket(), 2000);
    };

    ws.onerror = (error) => {
        console.error('Websocket error:', error);
        showStatus('Websocket connection error', 'error');
    };

    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        console.log('Received message:', message);

        if (message.id && pendingRequests.has(message.id)) {
            const { resolve, reject } = pendingRequests.get(message.id);
            pendingRequests.delete(message.id);

            if (message.error) {
                reject(message.error);
            } else {
                resolve(message.result);
            }
        }
    };
}

function sendRPC(method, params = {}) {
    return new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reject(new Error('Websocket not connected'));
            return;
        }

        const id = requestId++;
        const message = {
            jsonrpc: '2.0',
            method,
            params,
            id
        };

        pendingRequests.set(id, { resolve, reject });
        ws.send(JSON.stringify(message));

        // Timeout after 30 seconds
        setTimeout(() => {
            if (pendingRequests.has(id)) {
                pendingRequests.delete(id);
                reject(new Error('Request timeout'));
            }
        }, 30000);
    });
}

async function sendGcode(gcode) {
    console.trace('sendGcode called with:', gcode.substring(0, 50) + '...');
    try {
        const result = await sendRPC('printer.gcode.script', { script: gcode });
        console.log('sendGcode result:', result);
        return result;
    } catch (error) {
        // Parse Klipper error messages (prefixed with !!)
        if (error.message && error.message.includes('!!')) {
            const match = error.message.match(/!!\s*(.+)/);
            if (match) {
                throw new Error(match[1]);
            }
        }
        throw error;
    }
}

async function queryPrinterObjects(objects) {
    try {
        // Subscribe once on first query to ensure we get fresh data
        if (!subscribed) {
            await sendRPC('printer.objects.subscribe', { objects });
            subscribed = true;
        }

        // Query for current status
        const result = await sendRPC('printer.objects.query', { objects });
        return result.status;
    } catch (error) {
        console.error('Failed to query printer objects:', error);
        throw error;
    }
}

// ============================================================================
// Channel Management
// ============================================================================

async function refreshAllChannels() {
    console.trace('refreshAllChannels called from:');

    if (refreshing) {
        console.warn('Refresh already in progress, skipping');
        return;
    }

    const refreshBtn = document.getElementById('refresh-all');

    if (!wsReady) {
        showStatus('Waiting for websocket connection...', 'info');
        return;
    }

    refreshing = true;

    // Disable button and show loading state
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Refreshing...';
    }

    try {
        showStatus('Refreshing extruders...', 'info');

        // Build a combined gcode script for all clear and update commands
        // This executes them as a single transaction which is more efficient
        const gcodeScript = [
            'FILAMENT_DT_CLEAR CHANNEL=0',
            'FILAMENT_DT_CLEAR CHANNEL=1',
            'FILAMENT_DT_CLEAR CHANNEL=2',
            'FILAMENT_DT_CLEAR CHANNEL=3',
            'FILAMENT_DT_UPDATE CHANNEL=0',
            'FILAMENT_DT_UPDATE CHANNEL=1',
            'FILAMENT_DT_UPDATE CHANNEL=2',
            'FILAMENT_DT_UPDATE CHANNEL=3'
        ].join('\n');

        // Send all commands as a single gcode script
        console.log('Sending gcode script:', gcodeScript);
        await sendGcode(gcodeScript);
        console.log('Gcode script sent');

        // Wait for detection to complete before querying (2 seconds for all 4 channels)
        await new Promise(resolve => setTimeout(resolve, 2000));

        // Query filament_detect object for all channels
        const status = await queryPrinterObjects({ filament_detect: ['info'] });
        console.log('Query result:', status);
        const detectInfo = status.filament_detect?.info;

        if (!detectInfo) {
            console.error('No detectInfo in status:', status);
            throw new Error('Failed to get filament detect info');
        }

        console.log('detectInfo:', detectInfo);

        // Parse channel data
        channelsData = [];
        for (let i = 0; i < 4; i++) {
            const channelInfo = detectInfo[i] || {};
            const hasUid = channelInfo.CARD_UID && channelInfo.CARD_UID.length > 0;
            const mainType = channelInfo.MAIN_TYPE && channelInfo.MAIN_TYPE !== 'NONE' ? channelInfo.MAIN_TYPE : null;
            const tagStatus = channelInfo.TAG_STATUS || null; // 'empty', 'error', or null
            const tagCC = channelInfo.TAG_CC || null;
            channelsData.push({
                channel: i,
                present: hasUid,
                uid: channelInfo.CARD_UID || [],
                card_type: channelInfo.CARD_TYPE || null,
                cc: tagCC,
                empty: hasUid && !mainType && tagStatus !== 'error',
                malformed: hasUid && !mainType && tagStatus === 'error',
                filament: {
                    type: mainType,
                    brand: channelInfo.VENDOR && channelInfo.VENDOR !== 'NONE' ? channelInfo.VENDOR : (channelInfo.MANUFACTURER && channelInfo.MANUFACTURER !== 'NONE' ? channelInfo.MANUFACTURER : null),
                    subtype: channelInfo.SUB_TYPE && channelInfo.SUB_TYPE !== 'NONE' ? channelInfo.SUB_TYPE : null,
                    color_hex: channelInfo.RGB_1 ? channelInfo.RGB_1.toString(16).padStart(6, '0').toUpperCase() : null,
                    alpha: channelInfo.ALPHA || 0xFF,
                    color2: channelInfo.RGB_2 || null,
                    color3: channelInfo.RGB_3 || null,
                    color4: channelInfo.RGB_4 || null,
                    color5: channelInfo.RGB_5 || null,
                    diameter: channelInfo.DIAMETER ? channelInfo.DIAMETER / 100.0 : null,
                    density: channelInfo.DENSITY || null,
                    min_temp: channelInfo.HOTEND_MIN_TEMP || null,
                    max_temp: channelInfo.HOTEND_MAX_TEMP || null,
                    bed_min_temp: channelInfo.BED_MIN_TEMP || null,
                    bed_max_temp: channelInfo.BED_MAX_TEMP || null,
                    weight: channelInfo.WEIGHT || null
                }
            });
        }

        renderChannels();
        showStatus('Extruders refreshed successfully', 'success');
    } catch (error) {
        console.error('Failed to refresh channels:', error);
        showStatus(`Failed to refresh extruders: ${error.message}`, 'error');
    } finally {
        // Re-enable button and restore text
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = 'Refresh All Extruders';
        }
        refreshing = false;
    }
}

async function refreshSingleChannel(channel) {
    // Refresh only the specified channel
    if (!wsReady) {
        showStatus('Waiting for websocket connection...', 'info');
        return;
    }

    try {
        // Build a gcode script to clear and update just this channel
        const gcodeScript = [
            `FILAMENT_DT_CLEAR CHANNEL=${channel}`,
            `FILAMENT_DT_UPDATE CHANNEL=${channel}`
        ].join('\n');

        // Send commands for this channel only
        await sendGcode(gcodeScript);

        // Wait for detection to complete before querying (1 second for single channel)
        await new Promise(resolve => setTimeout(resolve, 1000));

        // Query filament_detect object for this channel
        const status = await queryPrinterObjects({ filament_detect: ['info'] });
        const detectInfo = status.filament_detect?.info;

        if (!detectInfo) {
            throw new Error('Failed to get filament detect info');
        }

        // Update just this channel's data
        const channelInfo = detectInfo[channel] || {};
        const mainType = channelInfo.MAIN_TYPE && channelInfo.MAIN_TYPE !== 'NONE' ? channelInfo.MAIN_TYPE : null;
        const filament = {
            type: mainType,
            brand: channelInfo.VENDOR && channelInfo.VENDOR !== 'NONE' ? channelInfo.VENDOR : (channelInfo.MANUFACTURER && channelInfo.MANUFACTURER !== 'NONE' ? channelInfo.MANUFACTURER : null),
            subtype: channelInfo.SUB_TYPE && channelInfo.SUB_TYPE !== 'NONE' ? channelInfo.SUB_TYPE : null,
            color_hex: channelInfo.RGB_1 ? channelInfo.RGB_1.toString(16).padStart(6, '0').toUpperCase() : null,
            alpha: channelInfo.ALPHA || 0xFF,
            color2: channelInfo.RGB_2 || null,
            color3: channelInfo.RGB_3 || null,
            color4: channelInfo.RGB_4 || null,
            color5: channelInfo.RGB_5 || null,
            diameter: channelInfo.DIAMETER ? channelInfo.DIAMETER / 100.0 : null,
            density: channelInfo.DENSITY || null,
            min_temp: channelInfo.HOTEND_MIN_TEMP || null,
            max_temp: channelInfo.HOTEND_MAX_TEMP || null,
            bed_min_temp: channelInfo.BED_MIN_TEMP || null,
            bed_max_temp: channelInfo.BED_MAX_TEMP || null,
            weight: channelInfo.WEIGHT || null
        };

        // Find and update the channel in our data
        const hasUid = channelInfo.CARD_UID && channelInfo.CARD_UID.length > 0;
        const tagStatus = channelInfo.TAG_STATUS || null;
        const tagCC = channelInfo.TAG_CC || null;
        const channelIdx = channelsData.findIndex(c => c.channel === channel);
        if (channelIdx !== -1) {
            channelsData[channelIdx] = {
                channel: channel,
                present: hasUid,
                uid: channelInfo.CARD_UID || [],
                card_type: channelInfo.CARD_TYPE || null,
                cc: tagCC,
                empty: hasUid && !mainType && tagStatus !== 'error',
                malformed: hasUid && !mainType && tagStatus === 'error',
                filament: filament
            };
        }

        // Re-render just to update this channel's card
        renderChannels();
        showStatus(`Extruder ${channel + 1} refreshed successfully`, 'success');
    } catch (error) {
        console.error(`Failed to refresh channel ${channel}:`, error);
        showStatus(`Failed to refresh extruder ${channel + 1}: ${error.message}`, 'error');
    }
}

function renderChannels() {
    console.log('renderChannels called, channelsData:', channelsData);
    const grid = document.getElementById('channels-grid');
    grid.innerHTML = '';

    channelsData.forEach(channel => {
        const card = createChannelCard(channel);
        grid.appendChild(card);
    });
}

// ============================================================================
// Channel Card Rendering
// ============================================================================

function createChannelCard(channel) {
    const card = document.createElement('div');
    card.className = 'channel-card';
    card.dataset.channel = channel.channel;

    const hasTag = channel.present;
    const isEmpty = channel.empty;
    const isMalformed = channel.malformed;
    const filament = channel.filament;

    // Collect non-critical warnings for this tag
    const tagWarnings = [];
    if (hasTag && channel.cc && channel.card_type === 'NTAG215') {
        const NTAG215_CC = [0xE1, 0x10, 0x3F, 0x00];
        const NTAG216_CC = [0xE1, 0x10, 0x6D, 0x00];
        const ccMatch = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
        if (!ccMatch(channel.cc, NTAG215_CC) && !ccMatch(channel.cc, NTAG216_CC)) {
            const ccHex = channel.cc.map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
            tagWarnings.push(
                `Warning: Capability Container (CC) mismatch [${ccHex}]. ` +
                `Expected [E1 10 3F 00] or [E1 10 6D 00]. ` +
                `CC is write-once (OTP) — the firmware will attempt to correct it on the next write, ` +
                `but if extra bits are already set, correction may not be possible. ` +
                `If you experience issues, use a fresh tag.`);
        }
    }

    // Header
    const header = document.createElement('div');
    header.className = 'channel-header';
    const displayChannel = channel.channel + 1;
    let badgeClass, badgeText;
    if (isMalformed) {
        badgeClass = 'tag-error';
        badgeText = 'Tag Error';
    } else if (isEmpty) {
        badgeClass = 'tag-empty-data';
        badgeText = 'Empty Tag';
    } else if (hasTag) {
        badgeClass = 'tag-present';
        badgeText = 'Tag Present';
    } else {
        badgeClass = 'tag-empty';
        badgeText = 'No Tag';
    }

    // Build the status badge — split with tooltip if there are warnings
    let statusBadgeHtml;
    if (tagWarnings.length > 0) {
        const tooltipLines = tagWarnings.map(w => `<div>${w}</div>`).join('');
        // Use CSS custom property to set the base color for the split gradient
        const baseColorVar = getComputedStyle(document.documentElement)
            .getPropertyValue(`--${badgeClass === 'tag-present' ? 'success' : badgeClass === 'tag-empty-data' ? 'info' : badgeClass === 'tag-error' ? 'warning' : 'secondary'}-color`).trim();
        statusBadgeHtml = `
            <span class="badge-tooltip-wrap">
                <span class="tag-status tag-warning-split" style="--badge-base-color: ${baseColorVar}">
                    ${badgeText}
                </span>
                <span class="badge-tooltip">${tooltipLines}</span>
            </span>`;
    } else {
        statusBadgeHtml = `<span class="tag-status ${badgeClass}">${badgeText}</span>`;
    }

    header.innerHTML = `
        <h3>Extruder ${displayChannel}</h3>
        <span class="tag-badges">
            ${statusBadgeHtml}
        </span>
    `;
    card.appendChild(header);

    // Tag info
    if (hasTag) {
        const info = document.createElement('div');
        info.className = 'tag-info';

        // UID
        const uidHex = channel.uid.map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(':');
        info.innerHTML = `<div class="info-row"><strong>UID:</strong> ${uidHex}</div>`;

        // Card type
        if (channel.card_type) {
            info.innerHTML += `<div class="info-row"><strong>Type:</strong> ${channel.card_type}</div>`;
        }

        // Tag status messages
        if (isEmpty) {
            info.innerHTML += `<div class="tag-info-msg">Tag is blank and ready to be programmed.</div>`;
        } else if (isMalformed) {
            info.innerHTML += `<div class="tag-warning">Tag contains invalid or unrecognized data.</div>`;
        }

        // Filament info
        if (filament.type) {
            const brand = filament.brand || 'Unknown';
            const type = filament.type;
            const subtype = filament.subtype && filament.subtype !== 'Basic' ? ` (${filament.subtype})` : '';
            info.innerHTML += `<div class="info-row"><strong>Material:</strong> ${brand} ${type}${subtype}</div>`;

            // Color
            if (filament.color_hex) {
                const alpha = filament.alpha || 0xFF;
                const alphaStr = alpha < 0xFF ? ` (${(alpha / 255 * 100).toFixed(0)}%)` : '';
                const colorSwatch = `<span class="color-swatch" style="background-color: #${filament.color_hex}${alpha.toString(16).padStart(2, '0')}" title="#${filament.color_hex}"></span>`;
                let colorHtml = `<strong>Color:</strong>`;

                // Build color section with additional colors and primary color on right
                let colorsOnRight = '';

                // Additional colors on right
                const additionalColors = [filament.color2, filament.color3, filament.color4, filament.color5].filter(c => c && c !== 0);
                if (additionalColors.length > 0) {
                    const swatches = additionalColors.map(c => {
                        const hex = c.toString(16).padStart(6, '0').toUpperCase();
                        return `<span class="color-swatch" style="background-color: #${hex}" title="#${hex}"></span>`;
                    }).join('');
                    colorsOnRight = swatches;
                }

                // Primary color swatch and hex (with padding separator from secondary colors)
                const primaryColorSection = `${colorSwatch} #${filament.color_hex}${alphaStr}`;
                colorsOnRight += (colorsOnRight ? `<span class="color-separator"></span>` : '') + primaryColorSection;

                colorHtml += ` <span class="color-hex-primary">${colorsOnRight}</span>`;

                info.innerHTML += `<div class="info-row">${colorHtml}</div>`;
            }

            // Physical properties
            if (filament.diameter) {
                info.innerHTML += `<div class="info-row"><strong>Diameter:</strong> ${filament.diameter}mm</div>`;
            }
            if (filament.density) {
                info.innerHTML += `<div class="info-row"><strong>Density:</strong> ${filament.density} g/cm³</div>`;
            }

            // Temperatures
            if (filament.min_temp && filament.max_temp) {
                info.innerHTML += `<div class="info-row"><strong>Extruder:</strong> ${filament.min_temp}-${filament.max_temp}°C</div>`;
            }
            if (filament.bed_min_temp || filament.bed_max_temp) {
                const bedMin = filament.bed_min_temp || 0;
                const bedMax = filament.bed_max_temp || 0;
                info.innerHTML += `<div class="info-row"><strong>Bed:</strong> ${bedMin}-${bedMax}°C</div>`;
            }

            // Weight
            if (filament.weight) {
                info.innerHTML += `<div class="info-row"><strong>Weight:</strong> ${filament.weight}g</div>`;
            }
        }

        card.appendChild(info);
    }

    // Refresh button (always shown)
    const refreshButtonDiv = document.createElement('div');
    refreshButtonDiv.style.display = 'flex';
    refreshButtonDiv.style.gap = '8px';
    refreshButtonDiv.style.marginTop = '15px';
    refreshButtonDiv.innerHTML = `<button class="btn btn-secondary btn-channel-refresh" data-channel="${channel.channel}" style="flex: 1;">Refresh Extruder ${displayChannel}</button>`;
    card.appendChild(refreshButtonDiv);

    // Action buttons
    const actions = document.createElement('div');
    actions.className = 'channel-actions';

    if (hasTag && channel.card_type === 'NTAG215') {
        let buttonsHtml = '';

        if (filament.type) {
            // Tag has valid data - show Update and Erase
            buttonsHtml = `
                <button class="btn btn-primary btn-update" data-channel="${channel.channel}">Update</button>
                <button class="btn btn-danger btn-erase" data-channel="${channel.channel}">Erase</button>
            `;

            // Export and Import buttons
            buttonsHtml += `<button class="btn btn-info btn-export" data-channel="${channel.channel}">Export</button>`;
            buttonsHtml += `<button class="btn btn-info btn-import" data-channel="${channel.channel}">Import</button>`;
        } else if (isMalformed) {
            // Tag has invalid/unrecognized data - show Create, Erase, and Import
            buttonsHtml = `
                <button class="btn btn-success btn-create" data-channel="${channel.channel}">Create</button>
                <button class="btn btn-danger btn-erase" data-channel="${channel.channel}">Erase</button>
                <button class="btn btn-info btn-import" data-channel="${channel.channel}">Import</button>
            `;
        } else {
            // Tag is empty/blank - show Create and Import
            buttonsHtml = `
                <button class="btn btn-success btn-create" data-channel="${channel.channel}">Create</button>
                <button class="btn btn-info btn-import" data-channel="${channel.channel}">Import</button>
            `;
        }

        actions.innerHTML = buttonsHtml;
    } else if (hasTag && channel.card_type === 'M1') {
        // M1 tags - show export only if has data
        let buttonsHtml = `<div class="info-row"><em>M1 tags cannot be modified</em></div>`;
        if (filament.type) {
            buttonsHtml += `<button class="btn btn-info btn-export" data-channel="${channel.channel}">Export</button>`;
        }
        actions.innerHTML = buttonsHtml;
    } else if (hasTag) {
        // Unknown tag type
        actions.innerHTML = `<div class="info-row"><em>Unknown tag type</em></div>`;
    } else {
        // No tag present - no action buttons
        actions.innerHTML = '';
    }

    card.appendChild(actions);

    // Attach event listeners
    const channelRefreshBtn = card.querySelector('.btn-channel-refresh');
    if (channelRefreshBtn) {
        channelRefreshBtn.addEventListener('click', () => refreshSingleChannel(channel.channel));
    }

    const createBtn = actions.querySelector('.btn-create');
    if (createBtn) {
        createBtn.addEventListener('click', () => openWriteModal(channel.channel, 'create'));
    }

    const updateBtn = actions.querySelector('.btn-update');
    if (updateBtn) {
        updateBtn.addEventListener('click', () => openWriteModal(channel.channel, 'update'));
    }

    const eraseBtn = actions.querySelector('.btn-erase');
    if (eraseBtn) {
        eraseBtn.addEventListener('click', () => openEraseModal(channel.channel));
    }

    const exportBtn = actions.querySelector('.btn-export');
    if (exportBtn) {
        exportBtn.addEventListener('click', () => exportTag(channel));
    }

    const importBtn = actions.querySelector('.btn-import');
    if (importBtn) {
        importBtn.addEventListener('click', () => importTag(channel.channel));
    }

    return card;
}

// ============================================================================
// Modal Management
// ============================================================================

function initializeModals() {
    const writeModal = document.getElementById('write-modal');
    const eraseModal = document.getElementById('erase-modal');

    // Close buttons
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', () => {
            writeModal.close();
            eraseModal.close();
        });
    });

    // Close on backdrop click
    writeModal.addEventListener('click', (e) => {
        if (e.target === writeModal) {
            writeModal.close();
        }
    });

    eraseModal.addEventListener('click', (e) => {
        if (e.target === eraseModal) {
            eraseModal.close();
        }
    });

    // Form submissions
    const writeForm = document.getElementById('write-form');
    writeForm.addEventListener('submit', handleWriteTag);

    const eraseForm = document.getElementById('erase-form');
    eraseForm.addEventListener('submit', handleEraseTag);
}

function openWriteModal(channel, mode) {
    currentModalChannel = channel;
    currentModalMode = mode;

    const modal = document.getElementById('write-modal');
    const form = document.getElementById('write-form');
    const modalTitle = document.getElementById('write-modal-title');

    // Update title (display 1-indexed)
    const displayChannel = channel + 1;
    modalTitle.textContent = mode === 'create' ? `Create Tag - Extruder ${displayChannel}` : `Update Tag - Extruder ${displayChannel}`;

    // Reset form
    form.reset();

    // Reset all color pickers to defaults
    if (colorPickers.main) {
        colorPickers.main.setColor('#FFFFFFFF', true);
        colorPickers.main.applyColor();
    }
    for (let i = 2; i <= 5; i++) {
        const key = `color${i}`;
        if (colorPickers[key]) {
            colorPickers[key].setColor('#FFFFFF', true);
            colorPickers[key].applyColor();
        }
    }

    // Clear modified tracking AFTER picker resets (resets may trigger change events)
    userModifiedColors.clear();

    // Clear additional color input values (picker resets write FFFFFF into them)
    for (let i = 2; i <= 5; i++) {
        const hexInput = document.getElementById(`color${i}-hex`);
        if (hexInput) hexInput.value = '';
    }

    // Set channel
    form.elements.channel.value = channel;

    // Show modal first so Pickr can render into visible DOM
    modal.showModal();

    // Defer form population to after modal is fully rendered
    setTimeout(() => {
        if (mode === 'update') {
            const channelData = channelsData.find(c => c.channel === channel);
            if (channelData && channelData.filament) {
                populateWriteForm(channelData.filament);
            }
        }
    }, 0);
}

function populateWriteForm(filament) {
    const form = document.getElementById('write-form');

    if (filament.type) form.elements.type.value = filament.type;
    if (filament.brand) form.elements.brand.value = filament.brand;
    if (filament.subtype) form.elements.subtype.value = filament.subtype;

    // Color
    if (filament.color_hex) {
        const alpha = (filament.alpha || 0xFF).toString(16).padStart(2, '0').toUpperCase();
        const colorHexAlpha = filament.color_hex + alpha;
        form.elements.color_hex.value = colorHexAlpha;
        if (colorPickers.main) {
            colorPickers.main.setColor('#' + colorHexAlpha, true);
            colorPickers.main.applyColor();
        }
    }

    // Additional colors
    [filament.color2, filament.color3, filament.color4, filament.color5].forEach((color, idx) => {
        if (color && color !== 0) {
            const colorHex = color.toString(16).padStart(6, '0').toUpperCase();
            const inputName = `color${idx + 2}`;
            form.elements[inputName].value = colorHex;
            if (colorPickers[inputName]) {
                colorPickers[inputName].setColor('#' + colorHex, true);
                colorPickers[inputName].applyColor();
            }
            userModifiedColors.add(inputName);
        }
    });

    if (filament.diameter) form.elements.diameter.value = filament.diameter;
    if (filament.density) form.elements.density.value = filament.density;
    if (filament.min_temp) form.elements.min_temp.value = filament.min_temp;
    if (filament.max_temp) form.elements.max_temp.value = filament.max_temp;
    if (filament.bed_min_temp) form.elements.bed_min_temp.value = filament.bed_min_temp;
    if (filament.bed_max_temp) form.elements.bed_max_temp.value = filament.bed_max_temp;
    if (filament.weight) form.elements.weight.value = filament.weight;
}

function openEraseModal(channel) {
    currentModalChannel = channel;

    const modal = document.getElementById('erase-modal');
    const form = document.getElementById('erase-form');

    // Reset form
    form.reset();
    form.elements.channel.value = channel;

    // Update text (display 1-indexed)
    document.getElementById('erase-channel-text').textContent = channel + 1;

    modal.showModal();
}

// ============================================================================
// Tag Operations
// ============================================================================

function toUrlSafeBase64(uint8Array) {
    const binStr = String.fromCharCode(...uint8Array);
    return btoa(binStr).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function handleWriteTag(e) {
    e.preventDefault();

    const form = e.target;
    const formData = new FormData(form);
    const channel = formData.get('channel');

    // Map form fields to PrintTag-Web's OpenSpool format
    const colorHex = formData.get('color_hex');
    let colorRgb, alphaHex;
    if (colorHex.length === 8) {
        colorRgb = colorHex.substring(0, 6);
        alphaHex = colorHex.substring(6, 8);
    } else {
        colorRgb = colorHex.length === 6 ? colorHex : 'FFFFFF';
        alphaHex = 'FF';
    }

    // Generate OpenSpool JSON using PrintTag-Web library
    const openspoolData = OpenSpool.generateData({
        materialType: formData.get('type'),
        colorHex: '#' + colorRgb,
        brand: formData.get('brand') || 'Generic',
        minTemp: formData.get('min_temp') || '',
        maxTemp: formData.get('max_temp') || '',
        bedTempMin: formData.get('bed_min_temp') || '',
        bedTempMax: formData.get('bed_max_temp') || '',
        extendedSubType: formData.get('subtype') || '',
    });

    // Add fields that OpenSpool.generateData doesn't handle
    if (alphaHex !== 'FF') {
        openspoolData.alpha = alphaHex;
    }

    const additionalColors = [];
    for (let i = 2; i <= 5; i++) {
        const colorVal = formData.get(`color${i}`);
        if (colorVal && colorVal.length === 6 && userModifiedColors.has(`color${i}`)) {
            additionalColors.push(colorVal.toUpperCase());
        }
    }
    if (additionalColors.length > 0) {
        openspoolData.additional_color_hexes = additionalColors;
    }

    const diameter = parseFloat(formData.get('diameter'));
    if (diameter) {
        openspoolData.diameter = diameter;
    }

    const density = formData.get('density');
    if (density) {
        openspoolData.density = parseFloat(density);
    }

    const weight = formData.get('weight');
    if (weight) {
        openspoolData.weight = parseInt(weight);
    }

    // Encode to NDEF binary
    const jsonBytes = new TextEncoder().encode(JSON.stringify(openspoolData));
    const ndefBytes = NDEF.serialize(jsonBytes, 'application/json');

    if (!ndefBytes) {
        showStatus('Failed to encode NDEF data', 'error');
        return;
    }

    // Strip the first 4 bytes (CC / Capability Container) since
    // FILAMENT_TAG_WRITE writes user data starting at page 4.
    // CC (page 3) is handled separately by the firmware.
    const tlvBytes = ndefBytes.slice(4);

    // Convert to URL-safe base64 for gcode transport
    const base64Str = toUrlSafeBase64(tlvBytes);
    const gcode = `FILAMENT_TAG_WRITE CHANNEL=${channel} DATA=${base64Str}`;

    try {
        showStatus('Writing tag...', 'info');
        await sendGcode(gcode);

        // Close modal
        document.getElementById('write-modal').close();

        // Refresh channel
        await refreshSingleChannel(parseInt(channel));

        showStatus('Tag written successfully', 'success');
    } catch (error) {
        console.error('Failed to write tag:', error);
        showStatus(`Failed to write tag: ${error.message}`, 'error');
    }
}

async function handleEraseTag(e) {
    e.preventDefault();

    const form = e.target;
    const formData = new FormData(form);
    const channel = formData.get('channel');

    if (!formData.get('confirm')) {
        showStatus('Please confirm erase operation', 'error');
        return;
    }

    const gcode = `FILAMENT_TAG_ERASE CHANNEL=${channel} CONFIRM=1`;

    try {
        showStatus('Erasing tag...', 'info');
        await sendGcode(gcode);

        // Close modal
        document.getElementById('erase-modal').close();

        // Refresh channel
        await refreshSingleChannel(parseInt(channel));

        showStatus('Tag erased successfully', 'success');
    } catch (error) {
        console.error('Failed to erase tag:', error);
        showStatus(`Failed to erase tag: ${error.message}`, 'error');
    }
}

// ============================================================================
// Export/Import
// ============================================================================

function exportTag(channel) {
    // Export tag data as OpenSpool JSON (matches the format written to NTAG)
    const filament = channel.filament;
    if (!filament.type) {
        showStatus('No filament data to export', 'error');
        return;
    }

    const payload = {
        protocol: 'openspool',
        version: '1.0',
        type: filament.type,
        brand: filament.brand || 'Generic',
    };

    if (filament.subtype && filament.subtype !== 'Basic' && filament.subtype !== 'Reserved') {
        payload.subtype = filament.subtype;
    }

    if (filament.color_hex) {
        payload.color_hex = '#' + filament.color_hex;
    }

    if (filament.alpha && filament.alpha < 0xFF) {
        payload.alpha = filament.alpha.toString(16).padStart(2, '0').toUpperCase();
    }

    // Additional colors as hex string array
    const additionalColors = [filament.color2, filament.color3, filament.color4, filament.color5]
        .filter(c => c && c !== 0)
        .map(c => c.toString(16).padStart(6, '0').toUpperCase());
    if (additionalColors.length > 0) {
        payload.additional_color_hexes = additionalColors;
    }

    if (filament.min_temp) payload.min_temp = String(filament.min_temp);
    if (filament.max_temp) payload.max_temp = String(filament.max_temp);
    if (filament.bed_min_temp) payload.bed_min_temp = String(filament.bed_min_temp);
    if (filament.bed_max_temp) payload.bed_max_temp = String(filament.bed_max_temp);

    payload.diameter = filament.diameter || 1.75;

    if (filament.density) payload.density = filament.density;
    if (filament.weight) payload.weight = filament.weight;

    // Create JSON string
    const jsonString = JSON.stringify(payload, null, 2);

    // Create blob and download
    const blob = new Blob([jsonString], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `filament-${channel.channel}-${filament.type.toLowerCase()}-${Date.now()}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    showStatus('Tag exported successfully', 'success');
}

function importTag(channel) {
    // Import tag data from JSON file
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.style.display = 'none';

    input.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        try {
            const text = await file.text();
            const payload = JSON.parse(text);

            // Validate required fields
            if (!payload.type || !payload.brand) {
                throw new Error('Missing required fields: type and brand');
            }

            // Parse OpenSpool format color
            const colorHexRaw = (payload.color_hex || 'FFFFFF').replace(/^#/, '');
            let alphaHex = 'FF';
            if (payload.alpha) {
                // alpha is a hex string (e.g. "22") or integer
                if (typeof payload.alpha === 'string') {
                    alphaHex = payload.alpha.toUpperCase();
                } else {
                    alphaHex = payload.alpha.toString(16).padStart(2, '0').toUpperCase();
                }
            }
            const colorHexAlpha = colorHexRaw.toUpperCase() + alphaHex;

            // Open modal via openWriteModal which handles picker resets
            openWriteModal(channel, 'create');

            // Defer population to after modal is rendered
            setTimeout(() => {
                const form = document.getElementById('write-form');
                form.querySelector('select[name="type"]').value = payload.type;
                form.querySelector('input[name="brand"]').value = payload.brand || 'Generic';
                form.querySelector('input[name="subtype"]').value = payload.subtype || '';
                form.querySelector('input[name="color_hex"]').value = colorHexAlpha;
                form.querySelector('input[name="diameter"]').value = payload.diameter || 1.75;
                form.querySelector('input[name="density"]').value = payload.density || '';
                form.querySelector('input[name="min_temp"]').value = payload.min_temp || '';
                form.querySelector('input[name="max_temp"]').value = payload.max_temp || '';
                form.querySelector('input[name="bed_min_temp"]').value = payload.bed_min_temp || '';
                form.querySelector('input[name="bed_max_temp"]').value = payload.bed_max_temp || '';
                form.querySelector('input[name="weight"]').value = payload.weight || '';

                // Update main color picker
                if (colorPickers.main) {
                    colorPickers.main.setColor('#' + colorHexAlpha, true);
                    colorPickers.main.applyColor();
                }

                // Additional colors from OpenSpool format
                const additionalColors = payload.additional_color_hexes || [];
                for (let i = 0; i < 4; i++) {
                    const inputName = `color${i + 2}`;
                    const hexInput = form.querySelector(`input[name="${inputName}"]`);
                    if (additionalColors[i]) {
                        const hex = additionalColors[i].replace(/^#/, '').toUpperCase();
                        hexInput.value = hex;
                        if (colorPickers[inputName]) {
                            colorPickers[inputName].setColor('#' + hex, true);
                            colorPickers[inputName].applyColor();
                        }
                        userModifiedColors.add(inputName);
                    }
                }

                // Update modal title
                document.getElementById('write-modal-title').textContent = `Import Tag - Extruder ${channel + 1}`;
            }, 50);

            showStatus('Tag data imported - review and click Write Tag to save', 'success');
        } catch (error) {
            console.error('Failed to import tag:', error);
            showStatus(`Failed to import tag: ${error.message}`, 'error');
        }
    });

    document.body.appendChild(input);
    input.click();
    document.body.removeChild(input);
}

// ============================================================================
// Color Pickers
// ============================================================================

function createPickr(el, defaultColor, hasAlpha = true) {
    return Pickr.create({
        el: el,
        theme: 'nano',
        container: document.getElementById('write-modal'),
        default: defaultColor,
        useAsButton: false,
        swatches: [
            '#FFFFFFFF', '#000000FF', '#FF0000FF', '#00FF00FF', '#0000FFFF',
            '#FFFF00FF', '#FF00FFFF', '#00FFFFFF', '#FFA500FF', '#808080FF'
        ],
        components: {
            preview: true,
            opacity: hasAlpha,
            hue: true,
            interaction: {
                hex: true,
                rgba: hasAlpha,
                input: true,
                save: false
            }
        }
    });
}

function initializeColorPickers() {
    const colorHex = document.getElementById('color-hex');

    // Main color picker with alpha
    colorPickers.main = createPickr('#color-picker', '#FFFFFFFF', true);

    colorPickers.main.on('change', (color) => {
        if (color) {
            const hexArr = color.toHEXA();
            const hexStr = hexArr.join('').toUpperCase();
            colorHex.value = hexStr;
            colorPickers.main.applyColor();
        }
    });

    colorHex.addEventListener('input', (e) => {
        const normalized = e.target.value.replace(/^#/, '').toUpperCase();
        e.target.value = normalized;
    });

    colorHex.addEventListener('blur', (e) => {
        const value = e.target.value;
        if (/^[0-9A-Fa-f]{6}$/.test(value)) {
            colorPickers.main.setColor('#' + value + 'FF', true);
            colorPickers.main.applyColor();
        } else if (/^[0-9A-Fa-f]{8}$/.test(value)) {
            colorPickers.main.setColor('#' + value, true);
            colorPickers.main.applyColor();
        }
    });

    // Additional color pickers (no alpha)
    for (let i = 2; i <= 5; i++) {
        const pickerEl = `#color${i}-picker`;
        const hexInput = document.getElementById(`color${i}-hex`);
        const colorKey = `color${i}`;

        colorPickers[colorKey] = createPickr(pickerEl, '#FFFFFF', false);

        colorPickers[colorKey].on('change', (color) => {
            if (color) {
                const hex = color.toHEXA().toString().replace(/^#/, '').substring(0, 6).toUpperCase();
                hexInput.value = hex;
                colorPickers[colorKey].applyColor();
                userModifiedColors.add(colorKey);
            }
        });

        hexInput.addEventListener('input', (e) => {
            const normalized = e.target.value.replace(/^#/, '').toUpperCase();
            e.target.value = normalized;
            if (normalized.length > 0) {
                userModifiedColors.add(colorKey);
            }
        });

        hexInput.addEventListener('blur', (e) => {
            const value = e.target.value;
            if (/^[0-9A-Fa-f]{6}$/.test(value)) {
                colorPickers[colorKey].setColor('#' + value, true);
                colorPickers[colorKey].applyColor();
            }
        });
    }

    // Material type change handler - auto-fill defaults
    const typeSelect = document.querySelector('#write-form select[name="type"]');
    if (typeSelect) {
        typeSelect.addEventListener('change', (e) => {
            const material = e.target.value;
            const defaults = MATERIAL_DEFAULTS[material];
            if (defaults) {
                const form = document.getElementById('write-form');
                if (!form.elements.min_temp.value) form.elements.min_temp.value = defaults.min_temp;
                if (!form.elements.max_temp.value) form.elements.max_temp.value = defaults.max_temp;
                if (!form.elements.bed_min_temp.value) form.elements.bed_min_temp.value = defaults.bed_min_temp;
                if (!form.elements.bed_max_temp.value) form.elements.bed_max_temp.value = defaults.bed_max_temp;
                if (!form.elements.density.value) form.elements.density.value = defaults.density;
            }
        });
    }
}

// ============================================================================
// Event Listeners
// ============================================================================

function initializeEventListeners() {
    // Refresh all button
    const refreshBtn = document.getElementById('refresh-all');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', refreshAllChannels);
    }
}

// ============================================================================
// UI Helpers
// ============================================================================

function showStatus(message, type = 'info') {
    const statusEl = document.getElementById('status-message');
    if (!statusEl) return;

    statusEl.textContent = message;
    statusEl.className = `status-message status-${type}`;

    // Use show class for CSS animation
    statusEl.classList.add('show');

    // Auto-hide after 5 seconds for success/info messages
    if (type === 'success' || type === 'info') {
        setTimeout(() => {
            statusEl.classList.remove('show');
        }, 5000);
    }
}
