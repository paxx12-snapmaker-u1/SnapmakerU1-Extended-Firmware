/* The fan curve editor of the settings page.
 *
 * The component owns its markup, its rules and its behaviour. The
 * settings page gives it a place in the fan curve setting and nothing
 * else, so the editor is not a second page that has to be kept in step
 * with the first one.
 */
(function () {
    'use strict';

    const MARKUP = `<div class="fc-editor">
<header class="editor-head">
    <div class="editor-head-left">
        <label for="profile">Profile</label>
        <select id="fc-profile" aria-label="Fan curve profile"></select>
        <input class="profile-name" id="fc-profile-name" type="text" maxlength="24" placeholder="name" hidden>
        <div class="menu-wrap">
            <button class="btn btn-icon" id="fc-profile-menu" type="button" aria-haspopup="true" aria-expanded="false" title="Profile actions">…</button>
            <div class="menu" id="fc-profile-actions" hidden>
                <button type="button" data-action="new">New profile</button>
                <button type="button" data-action="rename">Rename</button>
                <button type="button" data-action="delete">Delete</button>
                <button type="button" data-action="restore">Restore the shipped profile</button>
                <button type="button" data-action="discard">Discard changes</button>
            </div>
        </div>
        <span class="editor-active" id="fc-active"></span>
    </div>
    <div class="editor-tabs">
        <button class="btn btn-secondary" id="fc-power-tab" type="button">Power fan</button>
        <button class="btn btn-secondary" id="fc-nozzle-tab" type="button">Nozzle fans (×4)</button>
    </div>
    <span class="info" id="fc-info" tabindex="0">i</span>
</header>

<main class="editor-main">
    <section class="panel panel-chart">
        <div class="chart-wrap" id="fc-chart-wrap">
            <svg class="chart" id="fc-chart" tabindex="0" role="application" aria-label="Fan curve chart"></svg>
        </div>
        <div class="legend">
            <span class="legend-item"><span class="swatch"></span><span id="fc-legend-selected">Curve</span></span>
            <span class="legend-item" id="fc-legend-stock" hidden><span class="swatch stock"></span>Stock</span>
        </div>
        <p class="chart-note" id="fc-hint"></p>
    </section>
</main>

<div class="editor-toolbar">
    <span class="strip-title" id="fc-point-title">Selected point</span>
    <div class="strip-fields" id="fc-point-editor" hidden>
        <label class="strip-field">
            <span>Temperature</span>
            <input id="fc-point-temperature" type="number" step="1"><span class="unit">°C</span>
        </label>
        <label class="strip-field">
            <span>Duty</span>
            <input id="fc-point-duty" type="number" step="0.1" min="0"><span class="unit">%</span>
        </label>
        <button class="btn btn-secondary strip-button" id="fc-point-delete" type="button">Delete point</button>
    </div>
    <span class="strip-hint" id="fc-point-empty"></span>
    <div class="status" id="fc-status" aria-live="polite"></div>
    <div class="actions">
        <button class="btn btn-secondary" id="fc-save" type="button">Save</button>
        <button class="btn btn-primary" id="fc-apply" type="button">Apply</button>
    </div>
</div>

<div class="backdrop" id="fc-confirm-backdrop" hidden>
    <div class="dialog">
        <div class="dialog-title" id="fc-confirm-title"></div>
        <div class="dialog-body" id="fc-confirm-body"></div>
        <div class="dialog-actions">
            <button class="btn btn-secondary" id="fc-confirm-cancel" type="button">Cancel</button>
            <button class="btn btn-primary" id="fc-confirm-ok" type="button">Confirm</button>
        </div>
    </div>
</div>

<div class="backdrop" id="fc-action-backdrop" hidden>
    <div class="dialog">
        <div class="dialog-title" id="fc-action-title"></div>
        <pre class="dialog-log" id="fc-action-log"></pre>
        <div class="dialog-actions">
            <button class="btn btn-primary" id="fc-action-close" type="button">Close</button>
        </div>
    </div>
</div>
</div>`;

    function createFanCurveEditor(options) {
        const settings = options || {};

        // The root of the component. The settings page gives it a place.
        let root = null;
        const $ = id => root.querySelector('#fc-' + id);

        const CHART_MARGIN = { left: 52, right: 18, top: 18, bottom: 34 };
        const CHART_MIN = { width: 320, height: 200 };
        // How far from a point a press still takes hold of it. The point is
        // drawn small, so the grab area is bigger than the dot.
        const POINT_GRAB_RADIUS = 14;

        let state = null;
        // The profile that is shown. It starts empty, so the editor opens on the
        // profile that is running.
        let selectedProfile = '';
        let activeCurve = 'power';
        let selectedNode = -1;
        // The working copy of every profile the editor has seen. A profile that was
        // not written to its file yet keeps its edits while another one is picked.
        let drafts = {};
        let drag = null;
        let pendingClick = null;
        let chartSize = { width: 760, height: 400 };
        let naming = null;

        // ---------------------------------------------------------------- helpers

        function profileKey(key) {
            return String(key || '').toLowerCase().replace(/[- ]/g, '_');
        }

        // The name the firmware gives a profile comes with the state, so the
        // editor and the settings page call a profile the same.
        function profileLabel(key) {
            const known = state?.labels?.[key];
            if (known) return known;
            return String(key || '').split('_').filter(Boolean)
                .map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
        }

        function pointsOf(value) {
            const list = Array.isArray(value) ? value : [];
            return list.map(point => [Number(point[0]), Number(point[1])])
                .filter(point => point.every(Number.isFinite));
        }

        function profile(value) {
            const source = value && typeof value === 'object' ? value : {};
            const curves = source.curves && typeof source.curves === 'object' ? source.curves : source;
            return {
                power: pointsOf(curves.power),
                nozzle: pointsOf(curves.nozzle)
            };
        }

        // The stock table is a step table: the duty of the last row at or below
        // the temperature.
        function step(points, temperature) {
            let duty = 0;
            for (const [threshold, value] of points) {
                if (temperature >= threshold) duty = value;
                else break;
            }
            return duty;
        }

        // A curve is a line between its points, the same way the driver reads it.
        function curveDuty(points, temperature) {
            if (!points.length) return 0;
            if (temperature < points[0][0]) return 0;
            const last = points[points.length - 1];
            if (temperature >= last[0]) return last[1];
            for (let index = 1; index < points.length; index++) {
                const [lowTemperature, lowDuty] = points[index - 1];
                const [highTemperature, highDuty] = points[index];
                if (temperature < highTemperature) {
                    const span = highTemperature - lowTemperature;
                    if (span <= 0) return highDuty;
                    return lowDuty + (highDuty - lowDuty) * (temperature - lowTemperature) / span;
                }
            }
            return last[1];
        }

        function maxPoints() {
            return Number(state?.limits?.max_points) || 16;
        }

        function maxDuty() {
            const value = Number(state?.limits?.duty?.[1]);
            return Number.isFinite(value) ? Math.min(value, 1) : 1;
        }

        // The range of temperature the printer accepts. The printer sends it
        // with the state, so the editor does not carry a number of its own.
        function temperatureRange() {
            const limits = state?.limits?.temperature;
            const low = Number(limits?.[0]);
            const high = Number(limits?.[1]);
            return {
                min: Number.isFinite(low) ? low : 0,
                max: Number.isFinite(high) ? high : 300
            };
        }

        function maxTemperature() {
            return temperatureRange().max;
        }

        function setStatus(message, kind = '') {
            const status = $('status');
            status.textContent = message || '';
            status.className = `status${kind ? ` ${kind}` : ''}`;
        }

        // ---------------------------------------------------------------- state

        function normalize(payload) {
            const files = {};
            Object.entries(payload.files || {}).forEach(([key, value]) => {
                const entry = value && typeof value === 'object' ? value : {};
                files[profileKey(key)] = {
                    curves: profile(entry),
                    error: String(entry.error || ''),
                    hysteresis: entry.hysteresis,
                    heavyLoad: entry.heavy_load && typeof entry.heavy_load === 'object' ? entry.heavy_load : {}
                };
            });
            const labels = {};
            Object.entries(payload.labels || {}).forEach(([key, value]) => {
                labels[profileKey(key)] = String(value);
            });
            return {
                current: profileKey(payload.current || 'stock'),
                profiles: (Array.isArray(payload.profiles) && payload.profiles.length
                    ? payload.profiles
                    : ['stock', ...Object.keys(files)]).map(profileKey),
                files,
                labels,
                notice: String(payload.notice || ''),
                stock: profile(payload.stock),
                limits: payload.limits || {}
            };
        }

        // The working copy of a profile. Stock has no file, so it is read-only.
        function draftFor(name) {
            if (!drafts[name]) {
                const source = name === 'stock'
                    ? state.stock
                    : ((state.files[name] || {}).curves || { power: [], nozzle: [] });
                drafts[name] = {
                    power: source.power.map(point => point.slice()),
                    nozzle: source.nozzle.map(point => point.slice()),
                    dirty: false
                };
            }
            return drafts[name];
        }

        // Every profile but the stock one is a file, so it can be edited, renamed
        // and deleted.
        function isEditable() {
            const entry = state?.files?.[selectedProfile];
            return selectedProfile !== 'stock' && !!entry && !entry.error;
        }

        // What is wrong with the file of the selected profile, if anything.
        function profileError() {
            return state?.files?.[selectedProfile]?.error || '';
        }

        function currentDraft() {
            return draftFor(selectedProfile);
        }

        // The points the chart shows for the selected profile and curve.
        function shownPoints() {
            if (selectedProfile === 'stock') return (state.stock || {})[activeCurve] || [];
            return currentDraft()[activeCurve] || [];
        }

        // ---------------------------------------------------------------- chart

        // The chart fills its panel, so its size comes from the panel.
        function measureChart() {
            const wrap = $('chart-wrap');
            const rect = wrap.getBoundingClientRect();
            return {
                width: Math.max(CHART_MIN.width, Math.round(rect.width)),
                height: Math.max(CHART_MIN.height, Math.round(rect.height))
            };
        }

        function chartGeometry() {
            const { width, height } = chartSize;
            const margin = CHART_MARGIN;
            const plotWidth = width - margin.left - margin.right;
            const plotHeight = height - margin.top - margin.bottom;
            return {
                width, height, margin, plotWidth, plotHeight,
                toX: temperature => margin.left + (temperature / maxTemperature()) * plotWidth,
                toY: duty => margin.top + (1 - duty) * plotHeight,
                fromX: x => Math.max(0, Math.min(maxTemperature(),
                    ((x - margin.left) / plotWidth) * maxTemperature())),
                fromY: y => Math.max(0, Math.min(1, 1 - ((y - margin.top) / plotHeight)))
            };
        }

        // The fan is off below the first point.
        function curveStart(points) {
            return points.length ? points[0][0] : 0;
        }

        // The curve is sampled every degree, so the line that is drawn is the line
        // the driver follows.
        function sampleCurve(points, evaluate) {
            const samples = [];
            for (let temperature = curveStart(points); temperature <= maxTemperature(); temperature++) {
                samples.push([temperature, evaluate(points, temperature)]);
            }
            return samples;
        }

        function linePath(samples, geometry) {
            return samples.map((sample, index) => {
                const x = geometry.toX(sample[0]).toFixed(1);
                const y = geometry.toY(sample[1]).toFixed(1);
                return `${index === 0 ? 'M' : 'L'} ${x} ${y}`;
            }).join(' ');
        }

        function renderChart() {
            const svg = $('chart');
            if (!svg || !state) return;
            // The chart is rebuilt, so the point that had the keyboard focus has to
            // get it back.
            const focused = document.activeElement;
            const focusIndex = focused && focused.classList && focused.classList.contains('chart-point')
                ? focused.dataset.index : null;

            chartSize = measureChart();
            svg.setAttribute('viewBox', `0 0 ${chartSize.width} ${chartSize.height}`);
            const geometry = chartGeometry();
            const points = shownPoints();
            const referencePoints = selectedProfile === 'stock' ? [] : (state.stock || {})[activeCurve] || [];
            const editable = isEditable();

            const grid = [0, 0.2, 0.4, 0.6, 0.8, 1].map(duty => {
                const y = geometry.toY(duty);
                return `<line class="chart-grid" x1="${geometry.margin.left}" y1="${y}" x2="${geometry.width - geometry.margin.right}" y2="${y}"/>` +
                       `<text x="${geometry.margin.left - 8}" y="${y + 4}" text-anchor="end">${Math.round(duty * 100)}%</text>`;
            }).join('');
            const xLabels = [0, 1 / 3, 2 / 3, 1].map(part => {
                const temperature = Math.round(maxTemperature() * part);
                const x = geometry.toX(temperature);
                return `<line class="chart-grid" x1="${x}" y1="${geometry.margin.top}" x2="${x}" y2="${geometry.height - geometry.margin.bottom}"/>` +
                       `<text x="${x}" y="${geometry.height - 12}" text-anchor="middle">${temperature}°C</text>`;
            }).join('');

            const samples = sampleCurve(points, curveDuty);
            const dutyPath = linePath(samples, geometry);

            const referenceSamples = referencePoints.length ? sampleCurve(referencePoints, step) : [];
            const marks = points.map(([temperature, duty], index) => {
                const x = geometry.toX(temperature);
                const y = geometry.toY(duty);
                if (!editable) return `<circle class="chart-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3"></circle>`;
                const active = index === selectedNode;
                return `<circle class="chart-point${active ? ' selected' : ''}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${active ? 9 : 7}" data-index="${index}" tabindex="0" role="button" aria-label="Point ${index + 1}: ${temperature} degrees, ${Math.round(duty * 100)} percent"></circle>`;
            }).join('');

            const emptyText = profileError()
                ? 'The file of this profile cannot be read'
                : 'This profile has no curve';

            svg.innerHTML = `${grid}${xLabels}` +
                `<line class="chart-axis" x1="${geometry.margin.left}" y1="${geometry.height - geometry.margin.bottom}" x2="${geometry.width - geometry.margin.right}" y2="${geometry.height - geometry.margin.bottom}"/>` +
                `<line class="chart-axis" x1="${geometry.margin.left}" y1="${geometry.margin.top}" x2="${geometry.margin.left}" y2="${geometry.height - geometry.margin.bottom}"/>` +
                (referenceSamples.length ? `<path class="chart-reference" d="${linePath(referenceSamples, geometry)}"></path>` : '') +
                (dutyPath ? `<path class="chart-line" d="${dutyPath}"></path>` : '') +
                (points.length ? marks :
                    `<text x="${geometry.width / 2}" y="${geometry.height / 2}" text-anchor="middle">${emptyText}</text>`) +
                '<g class="chart-readout" id="fc-chart-readout"></g>';

            if (focusIndex !== null) {
                const next = svg.querySelector(`.chart-point[data-index="${focusIndex}"]`);
                if (next) next.focus();
            }

            $('legend-selected').textContent = profileLabel(selectedProfile);
            $('legend-stock').hidden = !referencePoints.length;
            $('hint').textContent = editable
                ? 'Click empty space to add a point, drag a point to move it, and remove it with Delete point or the right mouse button.'
                : (profileError()
                    ? 'The file of this profile cannot be read.'
                    : 'The stock curve is the original Snapmaker behaviour.');
        }

        // The readout under the cursor: the temperature, the duty of the curve and
        // the duty of the stock curve.
        function showReadout(temperature) {
            const group = $('chart-readout');
            if (!group || !state) return;
            if (temperature === null) {
                group.innerHTML = '';
                return;
            }
            const geometry = chartGeometry();
            const points = shownPoints();
            const own = curveDuty(points, temperature);
            const withStock = selectedProfile !== 'stock';
            const stock = step((state.stock || {})[activeCurve] || [], temperature);
            const text = `${Math.round(temperature)}°C   ${Math.round(own * 100)}%` +
                (withStock ? `   stock ${Math.round(stock * 100)}%` : '');
            const width = text.length * 6.2 + 14;
            const x = geometry.toX(temperature);
            const y = geometry.toY(own);
            const left = Math.max(geometry.margin.left,
                Math.min(geometry.width - geometry.margin.right - width, x - width / 2));
            group.innerHTML =
                `<line class="chart-guide" x1="${x.toFixed(1)}" y1="${geometry.margin.top}" x2="${x.toFixed(1)}" y2="${(geometry.height - geometry.margin.bottom).toFixed(1)}"/>` +
                `<circle class="chart-guide-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4"></circle>` +
                `<rect class="chart-readout-bg" x="${left.toFixed(1)}" y="${(geometry.margin.top + 4).toFixed(1)}" width="${width.toFixed(1)}" height="18" rx="4"></rect>` +
                `<text class="chart-readout-text" x="${(left + 7).toFixed(1)}" y="${(geometry.margin.top + 17).toFixed(1)}">${text}</text>`;
        }

        // ---------------------------------------------------------------- point editor

        function selectNode(index) {
            selectedNode = index;
            renderChart();
            renderPointEditor();
        }

        function renderPointEditor(force) {
            const editor = $('point-editor');
            const empty = $('point-empty');
            const points = currentDraft()[activeCurve] || [];
            if (!isEditable() || selectedNode < 0 || !points[selectedNode]) {
                editor.hidden = true;
                empty.hidden = false;
                $('point-title').textContent = 'Selected point';
                empty.textContent = isEditable()
                    ? 'Click a point on the chart to edit it.'
                    : 'The chart shows the duty the fan runs.';
                return;
            }
            const [temperature, duty] = points[selectedNode];
            editor.hidden = false;
            empty.hidden = true;
            $('point-title').textContent = `Point ${selectedNode + 1} of ${points.length}`;
            // A curve keeps two points, so the last one of them cannot go.
            $('point-delete').disabled = points.length <= 2;
            // A field that is being typed into keeps its text, otherwise the clamp
            // would rewrite the number while the user is still writing it.
            if (force || document.activeElement !== $('point-temperature')) {
                $('point-temperature').value = temperature;
            }
            if (force || document.activeElement !== $('point-duty')) {
                $('point-duty').value = (duty * 100).toFixed(1);
            }
        }

        // The neighbours bound the temperature, the driver keeps the order strict.
        function nodeBounds(index) {
            const points = currentDraft()[activeCurve] || [];
            const previous = index > 0 ? points[index - 1][0] : -1;
            const next = index < points.length - 1 ? points[index + 1][0] : maxTemperature() + 1;
            return { min: previous + 1, max: next - 1 };
        }

        function updateNode(index, temperature, duty) {
            const points = currentDraft()[activeCurve] || [];
            if (!points[index]) return;
            const bounds = nodeBounds(index);
            const clampedTemperature = Math.max(bounds.min, Math.min(bounds.max, Math.round(temperature)));
            const clampedDuty = Math.max(0, Math.min(maxDuty(), Math.round(duty * 1000) / 1000));
            if (points[index][0] === clampedTemperature && points[index][1] === clampedDuty) return;
            points[index] = [clampedTemperature, clampedDuty];
            currentDraft().dirty = true;
            renderChart();
            renderPointEditor();
        }

        function deleteNode() {
            const points = currentDraft()[activeCurve] || [];
            if (!isEditable() || selectedNode < 0 || !points[selectedNode]) return;
            if (points.length <= 2) {
                setStatus('A curve keeps at least two points.', 'error');
                return;
            }
            points.splice(selectedNode, 1);
            selectedNode = Math.min(selectedNode, points.length - 1);
            currentDraft().dirty = true;
            renderAll();
            setStatus('Point removed. Save the profile.');
        }

        // Adds a point at a free temperature. Returns the new index or -1.
        function addPoint(temperature, duty) {
            const points = currentDraft()[activeCurve] || [];
            if (!isEditable()) return -1;
            if (points.length >= maxPoints()) {
                setStatus(`A curve holds at most ${maxPoints()} points.`, 'error');
                return -1;
            }
            let index = points.findIndex(point => point[0] > temperature);
            if (index === -1) index = points.length;
            const previous = index > 0 ? points[index - 1][0] : -1;
            const next = index < points.length ? points[index][0] : maxTemperature() + 1;
            // The clicked temperature can land on a neighbour, which is one degree
            // away from a free spot. Move it there instead of refusing.
            let spot = temperature;
            if (spot <= previous) spot = previous + 1;
            if (spot >= next) spot = next - 1;
            if (spot <= previous || spot >= next) {
                setStatus('There is no room for another point between these two.', 'error');
                return -1;
            }
            points.splice(index, 0, [spot, duty]);
            selectedNode = index;
            currentDraft().dirty = true;
            renderAll();
            setStatus('Point added. Save the profile.');
            return index;
        }

        // ---------------------------------------------------------------- pointer

        // Where the pointer is, in the coordinates the chart is drawn in. The
        // chart keeps its own aspect ratio, so the drawing can be smaller than
        // the element it sits in. The transform of the chart is the truth about
        // that, so it is used instead of a division of two rectangles.
        function chartCoordinates(event) {
            const svg = $('chart');
            const matrix = typeof svg.getScreenCTM === 'function' ? svg.getScreenCTM() : null;
            if (matrix) {
                // The transform is undone by hand. A browser without DOMPoint
                // would throw here, and the editor would go dead in the middle
                // of a drag.
                const determinant = matrix.a * matrix.d - matrix.b * matrix.c;
                if (determinant) {
                    const dx = event.clientX - matrix.e;
                    const dy = event.clientY - matrix.f;
                    const x = (matrix.d * dx - matrix.c * dy) / determinant;
                    const y = (matrix.a * dy - matrix.b * dx) / determinant;
                    const geometry = chartGeometry();
                    return { x, y, temperature: geometry.fromX(x), duty: geometry.fromY(y) };
                }
            }
            const rect = svg.getBoundingClientRect();
            const scaleX = chartSize.width / rect.width;
            const scaleY = chartSize.height / rect.height;
            const x = (event.clientX - rect.left) * scaleX;
            const y = (event.clientY - rect.top) * scaleY;
            const geometry = chartGeometry();
            return { x, y, temperature: geometry.fromX(x), duty: geometry.fromY(y) };
        }

        // The point a press takes hold of. The dot is drawn small, so the grab
        // area around it is bigger, and the closest point wins, so two points
        // that are near each other are still the right one to take hold of.
        function pointAt(event) {
            if (!isEditable()) return -1;
            const points = shownPoints();
            if (!points.length) return -1;
            const position = chartCoordinates(event);
            const geometry = chartGeometry();
            let best = -1;
            let bestDistance = POINT_GRAB_RADIUS;
            points.forEach(([temperature, duty], index) => {
                const dx = geometry.toX(temperature) - position.x;
                const dy = geometry.toY(duty) - position.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                if (distance <= bestDistance) {
                    bestDistance = distance;
                    best = index;
                }
            });
            return best;
        }

        // A point is only added when the pointer was released on the chart, so a
        // press that is followed by a press on a button cannot add one.
        function pointerOnChart(event) {
            const rect = $('chart').getBoundingClientRect();
            return event.clientX >= rect.left && event.clientX <= rect.right &&
                   event.clientY >= rect.top && event.clientY <= rect.bottom;
        }

        // Holding the capture on the chart keeps the drag alive. Selecting the
        // point redraws the chart, which replaces the element the pointer
        // pressed, and a finger that lifts outside the window still reports.
        function capturePointer(pointerId) {
            const chart = $('chart');
            if (!chart || typeof chart.setPointerCapture !== 'function') return;
            try {
                chart.setPointerCapture(pointerId);
            } catch (error) {
                // The browser did not take the capture. The drag still works,
                // it only ends on the release outside the window.
            }
        }

        function releasePointer(pointerId) {
            const chart = $('chart');
            if (!chart || typeof chart.releasePointerCapture !== 'function') return;
            try {
                if (chart.hasPointerCapture(pointerId)) chart.releasePointerCapture(pointerId);
            } catch (error) {
                // The pointer is already gone.
            }
        }

        function onPointerDown(event) {
            if (!isEditable()) return;
            const index = pointAt(event);
            if (index >= 0) {
                event.preventDefault();
                capturePointer(event.pointerId);
                selectNode(index);
                startDrag(index, event.pointerId);
                return;
            }
            pendingClick = { x: event.clientX, y: event.clientY };
        }

        function startDrag(index, pointerId) {
            const point = (currentDraft()[activeCurve] || [])[index];
            drag = { index, pointerId, before: point ? point.slice() : null };
        }

        function onPointerMove(event) {
            if (pendingClick && Math.abs(event.clientX - pendingClick.x) + Math.abs(event.clientY - pendingClick.y) > 4) {
                pendingClick = null;
            }
            if (!drag) {
                showReadout(pointerOnChart(event) ? chartCoordinates(event).temperature : null);
                return;
            }
            if (drag.pointerId !== undefined && event.pointerId !== drag.pointerId) return;
            // A pointer that was released outside the window sends no pointerup.
            // The button state says that the drag is over.
            if (event.buttons === 0) {
                releasePointer(drag.pointerId);
                drag = null;
                showReadout(null);
                return;
            }
            const position = chartCoordinates(event);
            const points = currentDraft()[activeCurve] || [];
            if (!points[drag.index]) return;
            const bounds = nodeBounds(drag.index);
            const temperature = Math.max(bounds.min, Math.min(bounds.max, Math.round(position.temperature)));
            const duty = Math.max(0, Math.min(maxDuty(), Math.round(position.duty * 1000) / 1000));
            points[drag.index] = [temperature, duty];
            currentDraft().dirty = true;
            renderChart();
            renderPointEditor();
            showReadout(null);
        }

        function onPointerUp(event) {
            if (drag) {
                const moved = drag;
                drag = null;
                releasePointer(moved.pointerId);
                const point = (currentDraft()[activeCurve] || [])[moved.index];
                if (point && moved.before && (point[0] !== moved.before[0] || point[1] !== moved.before[1])) {
                    setStatus('Point moved. Save the profile.');
                }
                return;
            }
            if (!pendingClick) return;
            pendingClick = null;
            if (!isEditable() || !pointerOnChart(event)) return;
            const position = chartCoordinates(event);
            addPoint(Math.round(position.temperature), Math.round(position.duty * 1000) / 1000);
        }

        // A pointer that leaves the window never sends a pointerup, so the drag has
        // to be released here as well.
        function onPointerCancel(event) {
            if (drag) releasePointer(drag.pointerId);
            drag = null;
            pendingClick = null;
            showReadout(null);
        }

        // The right mouse button removes the point under it. A press that is
        // not on a point keeps the menu of the browser.
        function onContextMenu(event) {
            const index = pointAt(event);
            if (index < 0 || !isEditable()) return;
            event.preventDefault();
            selectNode(index);
            deleteNode();
        }

        function onKeyDown(event) {
            if (!isEditable()) return;
            const target = event.target.closest('.chart-point');
            if (!target) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    addPointInBiggestGap();
                }
                return;
            }
            const index = Number(target.dataset.index);
            const points = currentDraft()[activeCurve] || [];
            const point = points[index];
            if (!point) return;
            const step = event.shiftKey ? 5 : 1;
            if (event.key === 'ArrowLeft') {
                event.preventDefault();
                updateNode(index, point[0] - step, point[1]);
            } else if (event.key === 'ArrowRight') {
                event.preventDefault();
                updateNode(index, point[0] + step, point[1]);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                updateNode(index, point[0], point[1] + step / 100);
            } else if (event.key === 'ArrowDown') {
                event.preventDefault();
                updateNode(index, point[0], point[1] - step / 100);
            } else if (event.key === 'Delete' || event.key === 'Backspace') {
                event.preventDefault();
                selectNode(index);
                deleteNode();
            }
        }

        // Adds a point in the middle of the biggest gap of the curve.
        function addPointInBiggestGap() {
            const points = currentDraft()[activeCurve] || [];
            if (!isEditable()) return;
            if (points.length < 2) {
                addPoint(0, 0);
                return;
            }
            let gapIndex = 0;
            let gap = 0;
            for (let index = 0; index < points.length - 1; index++) {
                const currentGap = points[index + 1][0] - points[index][0];
                if (currentGap > gap) { gap = currentGap; gapIndex = index; }
            }
            if (gap < 2) {
                setStatus('There is no room for another point.', 'error');
                return;
            }
            const temperature = Math.round((points[gapIndex][0] + points[gapIndex + 1][0]) / 2);
            addPoint(temperature, Math.round((points[gapIndex][1] + points[gapIndex + 1][1]) * 500) / 1000);
        }

        // ---------------------------------------------------------------- dialogs

        let confirmAction = null;

        function showConfirm(title, message, onConfirm) {
            $('confirm-title').textContent = title;
            $('confirm-body').textContent = message;
            confirmAction = onConfirm;
            $('confirm-backdrop').hidden = false;
        }

        function closeConfirm() {
            $('confirm-backdrop').hidden = true;
            confirmAction = null;
        }

        function showAction(title) {
            $('action-title').textContent = title;
            $('action-log').textContent = '';
            $('action-backdrop').hidden = false;
        }

        function appendLog(text) {
            $('action-log').textContent += text;
        }

        // ---------------------------------------------------------------- server

        function getJWT() {
            const key = `user-token-${window.location.host.replace(/[^a-zA-Z0-9]/g, '_')}`;
            return localStorage.getItem(key);
        }

        async function apiFetch(url, options = {}) {
            const jwt = getJWT();
            options.headers = { ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}), ...options.headers };
            const response = await fetch(url, options);
            if (!response.ok) {
                const detail = await response.text().catch(() => '');
                throw new Error(`HTTP ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ''}`);
            }
            return response;
        }

        // A quiet call reports through the status line, a loud one opens the log
        // dialog that a Klipper restart needs.
        async function post(body, title, quiet) {
            if (!quiet) showAction(title);
            try {
                const response = await apiFetch('api/fan-curves', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const contentType = response.headers.get('content-type') || '';
                let result = null;
                if (contentType.includes('application/json')) {
                    result = await response.json();
                    if (result && result.success === false) throw new Error(result.error || 'The operation failed');
                    if (!quiet) appendLog(`${result?.message || 'Done'}\n`);
                } else if (!quiet) {
                    appendLog(await response.text());
                }
                return result;
            } catch (error) {
                if (!quiet) appendLog(`\nERROR: ${error.message}`);
                throw error;
            }
        }

        function tellPage(type) {
            if (typeof settings.onApplied === 'function') settings.onApplied(type);
        }

        // ---------------------------------------------------------------- rules

        function heavyLoadText() {
            // The rule is in the file, so it comes from the state. The
            // editor does not hold a copy of it.
            const heavy = state?.files?.[selectedProfile]?.heavyLoad || {};
            const temperature = heavy.temperature;
            const heaterCount = heavy.heater_count;
            const hotCount = heavy.hot_count;
            const duty = Math.round(heavy.duty * 100);
            const heaters = heaterCount > 0
                ? `at least ${heaterCount} toolhead${heaterCount === 1 ? '' : 's'} heat up and `
                : '';
            return `Heavy load: the power fan reaches ${duty}% when ${heaters}${hotCount} toolhead${hotCount === 1 ? '' : 's'} target more than ${temperature}°C.`;
        }

        function rulesText() {
            if (selectedProfile === 'stock') {
                // The stock profile has no file: the printer's own stepped
                // tables and its own rules are in control.
                return 'Stock is the printer\'s own stepped fan table. It has no file, so there is nothing to draw: the duty comes from the printer\'s own configuration.';
            }
            const entry = state?.files?.[selectedProfile];
            if (!entry || entry.error) {
                // A file that cannot be read carries no rules. The state
                // says what is wrong with it, and the editor does not
                // invent the rest.
                return 'The file of this profile cannot be read, so it has no rules.';
            }
            const hysteresis = entry.hysteresis;
            return `The duty is the line between the points. A curve uses ${hysteresis}°C hysteresis, so a falling temperature waits for the band before the duty follows. The fan is off below the first point, and the duty is what the curve says it is, up to ${Math.round(maxDuty() * 100)}%. ${heavyLoadText()}`;
        }

        // ---------------------------------------------------------------- profiles

        function fillProfileSelect() {
            const select = $('profile');
            select.innerHTML = '';
            state.profiles.forEach(key => {
                const option = document.createElement('option');
                option.value = key;
                option.textContent = profileLabel(key);
                select.appendChild(option);
            });
            select.value = selectedProfile;
        }

        function renderAll() {
            if (!state) return;
            fillProfileSelect();
            $('active').textContent = `Active: ${profileLabel(state.current)}`;
            $('power-tab').className = `btn ${activeCurve === 'power' ? 'btn-primary' : 'btn-secondary'}`;
            $('nozzle-tab').className = `btn ${activeCurve === 'nozzle' ? 'btn-primary' : 'btn-secondary'}`;
            $('info').title = rulesText();
            const range = temperatureRange();
            $('point-temperature').min = String(range.min);
            $('point-temperature').max = String(range.max);
            const editable = isEditable();
            $('save').disabled = !editable || !currentDraft().dirty;
            $('apply').disabled = false;
            $('profile-actions').querySelector('[data-action="rename"]').disabled = !editable;
            $('profile-actions').querySelector('[data-action="delete"]').disabled = !editable;
            $('profile-actions').querySelector('[data-action="discard"]').disabled = !currentDraft().dirty;
            const broken = profileError();
            if (broken) setStatus(broken, 'error');
            renderChart();
            renderPointEditor();
        }

        function selectProfile(name) {
            selectedProfile = name;
            selectedNode = -1;
            setStatus('');
            renderAll();
        }

        function startNaming(mode) {
            naming = mode;
            const input = $('profile-name');
            $('profile').hidden = true;
            input.hidden = false;
            input.value = mode === 'rename' ? selectedProfile : '';
            input.placeholder = mode === 'rename' ? 'new name' : 'name of the new profile';
            input.focus();
            input.select();
        }

        function stopNaming() {
            naming = null;
            $('profile-name').hidden = true;
            $('profile').hidden = false;
            $('profile').value = selectedProfile;
        }

        async function confirmNaming() {
            const mode = naming;
            if (!mode) return;
            const name = $('profile-name').value.trim();
            stopNaming();
            if (!name) {
                setStatus('A profile needs a name.', 'error');
                return;
            }
            const curves = {
                power: currentDraft().power.map(point => point.slice()),
                nozzle: currentDraft().nozzle.map(point => point.slice())
            };
            try {
                const result = await post({
                    action: mode === 'new' ? 'new' : 'rename',
                    name,
                    profile: selectedProfile,
                    curves
                }, null, true);
                const created = result?.profile || name;
                // A new profile leaves the profile that is on the screen alone. A
                // rename moves its file, so its working copy is stale.
                if (mode === 'rename') delete drafts[selectedProfile];
                await reloadState();
                selectProfile(created);
                setStatus(mode === 'new'
                    ? `${profileLabel(created)} created. Edit it and apply it when it is right.`
                    : `Renamed to ${profileLabel(created)}.`, 'success');
            } catch (error) {
                setStatus(error.message, 'error');
            }
        }

        async function deleteProfile() {
            if (!isEditable()) return;
            const name = selectedProfile;
            showConfirm(`Delete ${profileLabel(name)}?`,
                'The file is removed from the profile directory. A profile that is running is removed as well: the printer goes back to the stock curves first, so the running curve always has a file behind it.', async () => {
                closeConfirm();
                try {
                    await post({ action: 'delete', profile: name }, null, true);
                    delete drafts[name];
                    await reloadState();
                    selectProfile(state.current);
                    setStatus(`${profileLabel(name)} deleted.`, 'success');
                } catch (error) {
                    setStatus(error.message, 'error');
                }
            });
        }

        function restoreShippedProfile() {
            showConfirm('Restore the shipped profile',
                'The profile the firmware ships is written to the profile directory as Custom. A profile of your own named Custom is replaced by it.', async () => {
                closeConfirm();
                try {
                    const result = await post({ action: 'restore' }, null, true);
                    const name = result?.profile || 'custom';
                    delete drafts[name];
                    await reloadState();
                    selectProfile(name);
                    setStatus(`${profileLabel(name)} restored.`, 'success');
                } catch (error) {
                    setStatus(error.message, 'error');
                }
            });
        }

        function discardChanges() {
            delete drafts[selectedProfile];
            selectedNode = -1;
            renderAll();
            setStatus('The changes are discarded.');
        }

        // ---------------------------------------------------------------- actions

        function validateDraft() {
            const errors = [];
            const draft = currentDraft();
            ['power', 'nozzle'].forEach(name => {
                const points = draft[name] || [];
                if (points.length < 2) errors.push(`${name} needs at least two points`);
                if (points.length > maxPoints()) errors.push(`${name} has too many points`);
                points.forEach(([temperature, duty], index) => {
                    const range = temperatureRange();
                    if (!Number.isInteger(temperature) || temperature < range.min
                            || temperature > range.max) {
                        errors.push(`${name} point ${index + 1} has an invalid temperature`);
                    }
                    if (!Number.isFinite(duty) || duty < 0 || duty > maxDuty()) {
                        errors.push(`${name} point ${index + 1} exceeds the ordinary duty limit`);
                    }
                    if (index && temperature <= points[index - 1][0]) {
                        errors.push(`${name} temperatures must increase strictly`);
                    }
                });
            });
            return errors;
        }

        function saveProfile() {
            if (!isEditable()) return;
            const errors = validateDraft();
            if (errors.length) {
                setStatus(errors.join('; '), 'error');
                return;
            }
            const draft = currentDraft();
            showConfirm(`Save ${profileLabel(selectedProfile)}`,
                'Write the curve to your profile file?', async () => {
                closeConfirm();
                try {
                    await post({
                        action: 'save',
                        profile: selectedProfile,
                        curves: { power: draft.power, nozzle: draft.nozzle }
                    }, null, true);
                    currentDraft().dirty = false;
                    setStatus(`${profileLabel(selectedProfile)} saved.`, 'success');
                    renderAll();
                } catch (error) {
                    setStatus(error.message, 'error');
                }
            });
        }

        function applyProfile() {
            const errors = isEditable() ? validateDraft() : [];
            if (errors.length) {
                setStatus(errors.join('; '), 'error');
                return;
            }
            const body = { action: 'apply', profile: selectedProfile };
            if (isEditable()) {
                const draft = currentDraft();
                body.curves = { power: draft.power, nozzle: draft.nozzle };
            }
            const label = profileLabel(selectedProfile);
            const message = selectedProfile === 'stock'
                ? 'Applying the stock curves restarts Klipper and may interrupt a print. Continue?'
                : `${state.notice}\n\nApplying restarts Klipper and may interrupt a print. Continue?`;
            showConfirm(`Apply ${label}`, message, async () => {
                closeConfirm();
                try {
                    await post(body, `Apply ${label}`);
                    if (isEditable()) currentDraft().dirty = false;
                    setStatus(`${label} applied.`, 'success');
                    renderAll();
                    tellPage();
                } catch (error) {
                    setStatus(error.message, 'error');
                }
            });
        }

        async function reloadState() {
            const payload = await (await apiFetch('api/fan-curves')).json();
            state = normalize(payload);
            // A draft that was not written to its file follows the file again.
            Object.keys(drafts).forEach(name => {
                if (!drafts[name].dirty || !state.profiles.includes(name)) delete drafts[name];
            });
            if (!state.profiles.includes(selectedProfile)) selectedProfile = state.current;
            renderAll();
        }

        async function load() {
            try {
                await reloadState();
            } catch (error) {
                setStatus(`The fan curve API is unavailable: ${error.message}`, 'error');
            }
        }

        // ---------------------------------------------------------------- mount

        // The listeners that live outside the component are taken off again
        // when the component goes away.
        const cleanups = [];
        let observer = null;

        function on(target, type, handler, options) {
            target.addEventListener(type, handler, options);
            cleanups.push(() => target.removeEventListener(type, handler, options));
        }

        function bind() {
            $('profile').addEventListener('change', event => {
                selectProfile(event.target.value);
            });
            $('profile-name').addEventListener('keydown', event => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    confirmNaming();
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    stopNaming();
                }
            });
            $('profile-name').addEventListener('blur', () => {
                if (naming) stopNaming();
            });
            $('profile-menu').addEventListener('click', event => {
                event.stopPropagation();
                const menu = $('profile-actions');
                menu.hidden = !menu.hidden;
                $('profile-menu').setAttribute('aria-expanded', String(!menu.hidden));
            });
            on(document, 'click', event => {
                if (!$('profile-actions').hidden && !event.target.closest('.menu-wrap')) {
                    $('profile-actions').hidden = true;
                    $('profile-menu').setAttribute('aria-expanded', 'false');
                }
            });
            $('profile-actions').addEventListener('click', event => {
                const action = event.target.dataset.action;
                if (!action) return;
                $('profile-actions').hidden = true;
                $('profile-menu').setAttribute('aria-expanded', 'false');
                if (action === 'new') startNaming('new');
                else if (action === 'rename') startNaming('rename');
                else if (action === 'delete') deleteProfile();
                else if (action === 'restore') restoreShippedProfile();
                else if (action === 'discard') discardChanges();
            });
            $('power-tab').addEventListener('click', () => { activeCurve = 'power'; selectedNode = -1; renderAll(); });
            $('nozzle-tab').addEventListener('click', () => { activeCurve = 'nozzle'; selectedNode = -1; renderAll(); });
            $('save').addEventListener('click', saveProfile);
            $('apply').addEventListener('click', applyProfile);
            $('point-temperature').addEventListener('input', event => {
                if (!isEditable() || selectedNode < 0) return;
                const value = Number(event.target.value);
                if (!Number.isFinite(value)) return;
                const points = currentDraft()[activeCurve] || [];
                updateNode(selectedNode, value, points[selectedNode][1]);
            });
            $('point-duty').addEventListener('input', event => {
                if (!isEditable() || selectedNode < 0) return;
                const value = Number(event.target.value);
                if (!Number.isFinite(value)) return;
                const points = currentDraft()[activeCurve] || [];
                updateNode(selectedNode, points[selectedNode][0], value / 100);
            });
            $('point-temperature').addEventListener('change', () => {
                renderPointEditor(true);
            });
            $('point-duty').addEventListener('change', () => {
                renderPointEditor(true);
            });
            $('point-delete').addEventListener('click', () => {
                deleteNode();
            });
            $('confirm-ok').addEventListener('click', () => {
                const action = confirmAction;
                closeConfirm();
                if (action) action();
            });
            $('confirm-cancel').addEventListener('click', closeConfirm);
            $('action-close').addEventListener('click', () => { $('action-backdrop').hidden = true; });

            const chart = $('chart');
            chart.addEventListener('pointerdown', onPointerDown);
            chart.addEventListener('keydown', onKeyDown);
            chart.addEventListener('contextmenu', onContextMenu);
            chart.addEventListener('pointerleave', () => { if (!drag) showReadout(null); });
            on(window, 'pointermove', onPointerMove);
            on(window, 'pointerup', onPointerUp);
            on(window, 'pointercancel', onPointerCancel);
            on(window, 'blur', onPointerCancel);

            // A press on the chart is only a click to add a point when the
            // pointer is released on the chart as well. Any other press
            // cancels it.
            on(document, 'pointerdown', () => { pendingClick = null; }, true);

            // The chart fills the panel, so it is drawn again when the panel
            // changes.
            if (window.ResizeObserver) {
                observer = new ResizeObserver(() => renderChart());
                observer.observe($('chart-wrap'));
            }
            on(window, 'resize', renderChart);
        }

        function mount(host) {
            destroy();
            root = host;
            host.innerHTML = MARKUP;
            bind();
            load();
        }

        function destroy() {
            while (cleanups.length) cleanups.pop()();
            if (observer) {
                observer.disconnect();
                observer = null;
            }
            root = null;
        }

        function refresh() {
            if (!root || !$('chart')) return Promise.resolve();
            return reloadState().catch(error => {
                setStatus(error.message, 'error');
            });
        }

        return { mount, refresh, destroy };
    }

    window.FanCurveEditor = { create: createFanCurveEditor };
})();
