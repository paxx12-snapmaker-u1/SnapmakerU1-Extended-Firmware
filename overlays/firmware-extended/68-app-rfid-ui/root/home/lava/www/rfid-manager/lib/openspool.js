// OpenSpool format handler
// Simple JSON-based format for 3D printing filament tags

const OpenSpool = {
    // Base fields always visible in OpenSpool UI (normalized -> raw key; null means UI-only)
    BASE_FIELDS: {
        format: null, // Not stored, used for compatibility detection
        materialType: 'type',
        brand: 'brand',
        colorHex: 'color_hex'
    },

    // Compatibility profiles and associated conditional fields (normalized -> raw key)
    COMPATIBILITY_FIELDS: {
        openspool_compat: {},
        openspool_extended: {
            minTemp: 'min_temp',
            maxTemp: 'max_temp',
            bedTempMin: 'bed_min_temp',
            bedTempMax: 'bed_max_temp',
            // spoolmanId: 'spool_id', // disabled for now
            // lotNr: 'lot_nr', // disabled for now
            extendedSubType: 'subtype'
        }
    },

    // Return list of fields available based on formData (e.g., format)
    availableFields: function(formData) {
        const conditionalObj = this.COMPATIBILITY_FIELDS[formData.format] || {};
        const baseKeys = Object.keys(this.BASE_FIELDS);
        const conditionalKeys = Object.keys(conditionalObj);
        return new Set([ ...baseKeys, ...conditionalKeys ]);
    },

    // Get file extension for downloads
    getFileExtension(format) {
        return '.json';
    },


    // Generate OpenSpool JSON data
    generateData: function(formData) {
        const data = {
            protocol: "openspool",
            version: "1.0",
            type: formData.materialType,
            color_hex: formData.colorHex.replace('#', '').toUpperCase(),
        };

        if (formData.brand) {
            data.brand = formData.brand;
        }
        if (formData.minTemp) {
            data.min_temp = formData.minTemp;
        }
        if (formData.maxTemp) {
            data.max_temp = formData.maxTemp;
        }
        if (formData.bedTempMin) {
            data.bed_min_temp = formData.bedTempMin;
        }
        if (formData.bedTempMax) {
            data.bed_max_temp = formData.bedTempMax;
        }
        if (formData.spoolmanId && parseInt(formData.spoolmanId) !== 0) {
            data.spool_id = parseInt(formData.spoolmanId);
        }
        if (formData.lotNr) {
            data.lot_nr = formData.lotNr.toUpperCase();
        }
        if (formData.extendedSubType) {
            data.subtype = formData.extendedSubType;
        }

        return data;
    },

    // Parse OpenSpool data
    parseData: function(jsonData) {
        if (jsonData.protocol !== "openspool") {
            throw new Error("Not an OpenSpool format");
        }

        // Infer format by checking COMPATIBILITY_FIELDS against raw keys
        let format = 'openspool_compat';
        for (const [mode, fieldsObj] of Object.entries(this.COMPATIBILITY_FIELDS)) {
            if (mode === 'openspool_compat') continue;
            const anyPresent = Object.values(fieldsObj).some(raw =>
                raw && Object.prototype.hasOwnProperty.call(jsonData, raw)
            );
            if (anyPresent) {
                format = mode;
                break;
            }
        }

        return {
            format,
            materialType: jsonData.type || 'PLA',
            colorHex: jsonData.color_hex || 'FFFFFF',
            brand: jsonData.brand || 'Generic',
            minTemp: jsonData.min_temp || '220',
            maxTemp: jsonData.max_temp || '240',
            bedTempMin: jsonData.bed_min_temp || '',
            bedTempMax: jsonData.bed_max_temp || '',
            spoolmanId: jsonData.spool_id || 0,
            lotNr: jsonData.lot_nr || '',
            extendedSubType: jsonData.subtype || ''
        };
    },

    // Create NDEF record for NFC writing
    createNDEFRecord: function(data) {
        const jsonStr = JSON.stringify(data);
        const encoder = new TextEncoder();
        return [{
            recordType: "mime",
            mediaType: "application/json",
            data: encoder.encode(jsonStr)
        }];
    },

    // Parse NDEF record from NFC reading
    parseNDEFRecord: function(record) {
        if (record.recordType !== "mime" || record.mediaType !== "application/json") {
            return null;
        }

        const textDecoder = new TextDecoder();
        const text = textDecoder.decode(record.data);

        try {
            const jsonData = JSON.parse(text);
            if (jsonData.protocol === "openspool") {
                return this.parseData(jsonData);
            }
        } catch (e) {
            return null;
        }

        return null;
    },

    // Download as JSON file
    download: function(data, filename = 'openspool.json') {
        const jsonStr = JSON.stringify(data, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },

    // Read from text or binary buffer
    readBinary: function(buffer) {
        try {
            const text = typeof buffer === 'string' ? buffer : new TextDecoder().decode(buffer);
            const jsonData = JSON.parse(text);
            return this.parseData(jsonData);
        } catch (e) {
            console.error('Error reading OpenSpool JSON:', e);
            return null;
        }
    }
};
