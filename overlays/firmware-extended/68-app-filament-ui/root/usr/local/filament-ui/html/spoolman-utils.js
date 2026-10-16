(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.SpoolmanUtils = api;
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const MATERIAL_DENSITIES = Object.freeze({
        PLA: 1.24,
        PETG: 1.27,
        ABS: 1.04,
        ASA: 1.07,
        TPU: 1.21,
        NYLON: 1.12,
        PA: 1.12,
        PC: 1.19,
        PVA: 1.19,
        HIPS: 1.04,
        PP: 0.90,
    });

    function cleanText(value) {
        return value == null ? '' : String(value).trim();
    }

    function normalizeText(value) {
        return cleanText(value).toLowerCase();
    }

    function finiteNumber(value) {
        if (value === '' || value == null) return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function normalizeUid(value) {
        if (Array.isArray(value) || ArrayBuffer.isView(value)) {
            const bytes = Array.from(value);
            if (bytes.length === 0 || bytes.every(byte => Number(byte) === 0)) return '';
            if (bytes.some(byte => !Number.isInteger(Number(byte)) || Number(byte) < 0 || Number(byte) > 255)) return '';
            return bytes.map(byte => Number(byte).toString(16).padStart(2, '0')).join('').toUpperCase();
        }

        let uid = cleanText(value).replace(/^0x/i, '').replace(/[\s:,.-]/g, '');
        if (!uid || uid.length % 2 !== 0 || !/^[0-9a-f]+$/i.test(uid) || /^0+$/.test(uid)) return '';
        return uid.toUpperCase();
    }

    function decodeExtraString(value) {
        let decoded = cleanText(value);
        if (decoded.length >= 2 && decoded.startsWith('"') && decoded.endsWith('"')) {
            try {
                const parsed = JSON.parse(decoded);
                if (typeof parsed === 'string') decoded = parsed;
            } catch {
                decoded = decoded.slice(1, -1);
            }
        }
        return decoded;
    }

    function encodeExtraString(value) {
        return JSON.stringify(cleanText(value));
    }

    function uniqueUids(values) {
        const seen = new Set();
        const result = [];
        for (const value of values || []) {
            const uid = normalizeUid(value);
            if (uid && !seen.has(uid)) {
                seen.add(uid);
                result.push(uid);
            }
        }
        return result;
    }

    function parseCardUids(spool) {
        const raw = decodeExtraString(spool?.extra?.card_uids || '');
        return uniqueUids(raw.split(','));
    }

    function encodeCardUids(values) {
        return encodeExtraString(uniqueUids(values).join(','));
    }

    function parseVariant(filament) {
        return decodeExtraString(filament?.extra?.variant || '');
    }

    function canonicalVariant(value) {
        const variant = normalizeText(value);
        return variant === 'basic' ? '' : variant;
    }

    function normalizeColor(value) {
        const color = cleanText(value).replace(/^#/, '').toUpperCase();
        return /^[0-9A-F]{6}$/.test(color) ? color : '';
    }

    function materialDensity(material, reportedDensity) {
        const reported = finiteNumber(reportedDensity);
        if (reported != null && reported >= 0.5 && reported <= 3.0) return reported;
        return MATERIAL_DENSITIES[cleanText(material).toUpperCase()] || 1.24;
    }

    // Keep colour naming aligned with the SpoolLink companion app. It is only
    // a friendly, editable default; exact matching always uses the RGB value.
    function friendlyColorName(colorHex) {
        const hex = normalizeColor(colorHex);
        if (!hex) return null;
        const red = parseInt(hex.slice(0, 2), 16) / 255;
        const green = parseInt(hex.slice(2, 4), 16) / 255;
        const blue = parseInt(hex.slice(4, 6), 16) / 255;
        const max = Math.max(red, green, blue);
        const min = Math.min(red, green, blue);
        const delta = max - min;
        const lightness = (max + min) / 2;

        if (delta < 0.12) {
            if (lightness > 0.85) return 'White';
            if (lightness < 0.15) return 'Black';
            return 'Gray';
        }

        const saturation = lightness > 0.5
            ? delta / (2 - max - min)
            : delta / (max + min);
        if (saturation < 0.15) return lightness > 0.7 ? 'Silver' : 'Gray';

        let hue;
        if (max === red) {
            hue = ((green - blue) / delta) % 6;
            if (hue < 0) hue += 6;
        } else if (max === green) {
            hue = (blue - red) / delta + 2;
        } else {
            hue = (red - green) / delta + 4;
        }
        hue *= 60;

        if (hue >= 10 && hue < 40 && lightness < 0.45) return 'Brown';
        if (hue < 15) return 'Red';
        if (hue < 40) return 'Orange';
        if (hue < 65) return 'Yellow';
        if (hue < 165) return 'Green';
        if (hue < 195) return 'Cyan';
        if (hue < 255) return 'Blue';
        if (hue < 285) return 'Purple';
        if (hue < 325) return 'Magenta';
        if (hue < 345) return 'Pink';
        return 'Red';
    }

    function defaultFilamentName(metadata) {
        const parts = [
            cleanText(metadata?.material),
            cleanText(metadata?.variant),
            friendlyColorName(metadata?.color) || normalizeColor(metadata?.color),
        ].filter(Boolean);
        const deduped = parts.filter((part, index) => index === 0 || normalizeText(part) !== normalizeText(parts[index - 1]));
        return deduped.join(' ') || 'Custom Filament';
    }

    function isCreateEligible(channel) {
        const metadata = channel?.rfid_data || {};
        return !!(
            normalizeUid(channel?.uid) &&
            cleanText(metadata.vendor) &&
            cleanText(metadata.type) &&
            normalizeColor(metadata.color)
        );
    }

    function findMatchingFilaments(filaments, metadata) {
        const vendor = normalizeText(metadata?.vendor);
        const material = normalizeText(metadata?.material || metadata?.type);
        const variant = canonicalVariant(metadata?.variant || metadata?.sub_type);
        const color = normalizeColor(metadata?.color);
        if (!vendor || !material || !color) return [];

        return (filaments || []).filter(filament => (
            normalizeText(filament?.vendor?.name) === vendor &&
            normalizeText(filament?.material) === material &&
            canonicalVariant(parseVariant(filament)) === variant &&
            normalizeColor(filament?.color_hex) === color
        ));
    }

    function buildFilamentPayload(values, vendorId) {
        const diameter = finiteNumber(values?.diameter);
        const density = finiteNumber(values?.density);
        const color = normalizeColor(values?.color);
        const name = cleanText(values?.name);
        const material = cleanText(values?.material);
        if (!name) throw new Error('Filament name is required');
        if (!material) throw new Error('Material is required');
        if (!color) throw new Error('A valid six-digit colour is required');
        if (diameter == null || diameter <= 0) throw new Error('Diameter must be greater than zero');
        if (density == null || density <= 0) throw new Error('Density must be greater than zero');

        const payload = {
            name,
            material,
            color_hex: color,
            diameter,
            density,
        };
        if (Number.isInteger(Number(vendorId)) && Number(vendorId) > 0) payload.vendor_id = Number(vendorId);

        const weight = finiteNumber(values?.net_weight);
        const tare = finiteNumber(values?.tare_weight);
        const nozzle = finiteNumber(values?.nozzle_temp);
        const bed = finiteNumber(values?.bed_temp);
        if (weight != null && weight > 0) payload.weight = weight;
        if (tare != null && tare >= 0) payload.spool_weight = tare;
        if (nozzle != null && nozzle > 0) payload.settings_extruder_temp = Math.round(nozzle);
        if (bed != null && bed > 0) payload.settings_bed_temp = Math.round(bed);

        const variant = cleanText(values?.variant);
        if (variant) payload.extra = { variant: encodeExtraString(variant) };
        return payload;
    }

    function buildSpoolPayload(filamentId, values) {
        const id = Number(filamentId);
        const uid = normalizeUid(values?.uid);
        const netWeight = finiteNumber(values?.net_weight);
        const tareWeight = finiteNumber(values?.tare_weight);
        if (!Number.isInteger(id) || id <= 0) throw new Error('A valid filament is required');
        if (!uid) throw new Error('A valid RFID UID is required');
        if (netWeight == null || netWeight <= 0) throw new Error('Net weight must be greater than zero');

        const payload = {
            filament_id: id,
            initial_weight: netWeight,
            remaining_weight: netWeight,
            extra: { card_uids: encodeCardUids([uid]) },
        };
        if (tareWeight != null && tareWeight >= 0) payload.spool_weight = tareWeight;
        return payload;
    }

    function findUidOwners(spools, uidValue) {
        const uid = normalizeUid(uidValue);
        if (!uid) return [];
        return (spools || []).filter(spool => parseCardUids(spool).includes(uid));
    }

    function assignmentDecision(targetSpool, uidValue, owners) {
        const uid = normalizeUid(uidValue);
        const targetUids = parseCardUids(targetSpool);
        const targetId = Number(targetSpool?.id);
        const otherOwnerIds = (owners || [])
            .map(spool => Number(spool.id))
            .filter(id => Number.isInteger(id) && id !== targetId);

        let action = 'assign';
        let confirmLabel = 'Assign Spool';
        let requiresConfirmation = false;
        if (uid && targetUids.includes(uid) && otherOwnerIds.length === 0) {
            action = 'already_assigned';
        } else if (uid && otherOwnerIds.length > 0) {
            action = 'move';
            confirmLabel = 'Move RFID';
            requiresConfirmation = true;
        } else if (uid && targetUids.length === 0) {
            action = 'add_first';
            confirmLabel = 'Assign RFID';
        } else if (uid && targetUids.length === 1) {
            action = 'add_second';
            confirmLabel = 'Add Second RFID';
            requiresConfirmation = true;
        } else if (uid) {
            action = 'add_extra';
            confirmLabel = 'Add Another RFID';
            requiresConfirmation = true;
        }
        return { action, requiresConfirmation, confirmLabel, targetUids, otherOwnerIds };
    }

    function verifyUidAssignment(spools, targetIdValue, uidValue, previousTargetUids) {
        const targetId = Number(targetIdValue);
        const uid = normalizeUid(uidValue);
        const owners = findUidOwners(spools, uid);
        const target = (spools || []).find(spool => Number(spool.id) === targetId);
        const targetUids = parseCardUids(target);
        const missingPrevious = uniqueUids(previousTargetUids)
            .filter(previous => !targetUids.includes(previous));
        return {
            ok: !!target && owners.length === 1 && Number(owners[0].id) === targetId &&
                targetUids.includes(uid) && missingPrevious.length === 0,
            ownerIds: owners.map(spool => Number(spool.id)),
            targetUids,
            missingPrevious,
        };
    }

    return Object.freeze({
        assignmentDecision,
        buildFilamentPayload,
        buildSpoolPayload,
        cleanText,
        defaultFilamentName,
        encodeCardUids,
        findMatchingFilaments,
        findUidOwners,
        finiteNumber,
        isCreateEligible,
        materialDensity,
        normalizeColor,
        normalizeText,
        normalizeUid,
        parseCardUids,
        parseVariant,
        verifyUidAssignment,
    });
}));
