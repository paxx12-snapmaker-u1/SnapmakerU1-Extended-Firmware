// NDEF (NFC Data Exchange Format) utilities
const NDEF = (function() {
    // Serialize data to binary NDEF format with TLV structure
    function serialize(payload, mediaType) {
        const mediaTypeBytes = new TextEncoder().encode(mediaType);

        const typeLength = mediaTypeBytes.length;
        const payloadLength = payload.length;

        // Choose short or long record format based on payload size
        const isShortRecord = payloadLength < 256;
        const header = isShortRecord ? 0xD2 : 0xC2; // SR=1 or SR=0, MB=1, ME=1, TNF=0x02

        // Calculate record length
        const headerSize = 2 + (isShortRecord ? 1 : 4); // Header + Type Length + Payload Length
        const ndefRecordLength = headerSize + typeLength + payloadLength;
        const ndefRecord = new Uint8Array(ndefRecordLength);

        let offset = 0;
        ndefRecord[offset++] = header;
        ndefRecord[offset++] = typeLength;

        // Write payload length (1 or 4 bytes)
        if (isShortRecord) {
            ndefRecord[offset++] = payloadLength;
        } else {
            ndefRecord[offset++] = (payloadLength >> 24) & 0xFF;
            ndefRecord[offset++] = (payloadLength >> 16) & 0xFF;
            ndefRecord[offset++] = (payloadLength >> 8) & 0xFF;
            ndefRecord[offset++] = payloadLength & 0xFF;
        }

        // Write media type
        ndefRecord.set(mediaTypeBytes, offset);
        offset += typeLength;

        // Write payload
        ndefRecord.set(payload, offset);

        // Build full NDEF structure with TLV
        // CC (Capability Container): 4 bytes
        // NDEF Message TLV: Tag (0x03) + Length + Value (NDEF record)
        // Terminator TLV: 0xFE

        const cc = new Uint8Array([
            0xE1, // NDEF Magic Number
            0x10, // Version 1.0
            0x00, // Size (will be calculated)
            0x00  // Read/Write access
        ]);

        // Calculate total size in 8-byte blocks
        const ndefMessageLength = ndefRecordLength;
        const tlvLength = 2 + (ndefMessageLength < 255 ? 1 : 3) + ndefMessageLength + 1; // Tag + Length + Value + Terminator
        const totalSize = Math.ceil((4 + tlvLength) / 8);
        cc[2] = totalSize;

        // Build TLV structure
        const tlvData = [];
        tlvData.push(0x03); // NDEF Message TLV tag

        // Length encoding
        if (ndefMessageLength < 255) {
            tlvData.push(ndefMessageLength);
        } else {
            tlvData.push(0xFF);
            tlvData.push((ndefMessageLength >> 8) & 0xFF);
            tlvData.push(ndefMessageLength & 0xFF);
        }

        // NDEF record
        tlvData.push(...ndefRecord);

        // Terminator TLV
        tlvData.push(0xFE);

        // Combine CC + TLV
        const result = new Uint8Array(4 + tlvData.length);
        result.set(cc, 0);
        result.set(tlvData, 4);

        return result;
    }

    // Deserialize binary NDEF format with TLV structure
    function deserialize(buffer, mediaType) {
        try {
            const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : buffer;

            if (bytes.length < 7) {
                return null;
            }

            let offset = 0;

            // Look for capability container if not at start
            if (bytes[0] !== 0xE1) {
                let found = false;
                for (let i = 0; i < Math.min(16, bytes.length - 4); i++) {
                    if (bytes[i] === 0xE1) {
                        offset = i;
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    return null;
                }
            }

            // Read and verify Capability Container
            if (bytes[offset] !== 0xE1) {
                return null;
            }
            offset += 4; // Skip CC (4 bytes)

            // Parse TLV structures to find NDEF Message TLV (0x03)
            while (offset < bytes.length - 1) {
                const tag = bytes[offset++];

                if (tag === 0xFE) {
                    // Terminator TLV
                    return null;
                }

                let tlvLength = bytes[offset++];

                // Handle 3-byte length format
                if (tlvLength === 0xFF) {
                    if (offset + 2 > bytes.length) {
                        return null;
                    }
                    tlvLength = (bytes[offset] << 8) | bytes[offset + 1];
                    offset += 2;
                }

                if (tag === 0x03) {
                    // Found NDEF Message TLV
                    const ndefData = bytes.slice(offset, offset + tlvLength);

                    // Parse NDEF record(s) - may contain multiple records
                    let ndefOffset = 0;

                    while (ndefOffset < ndefData.length - 2) {
                        const header = ndefData[ndefOffset++];
                        const tnf = header & 0x07;
                        const sr = (header >> 4) & 0x01; // Short Record flag
                        const il = (header >> 3) & 0x01; // ID Length flag

                        const typeLength = ndefData[ndefOffset++];

                        // Read payload length (1 or 4 bytes depending on SR flag)
                        let payloadLength;
                        if (sr === 1) {
                            // Short record: 1-byte payload length
                            payloadLength = ndefData[ndefOffset++];
                        } else {
                            // Long record: 4-byte payload length
                            if (ndefOffset + 4 > ndefData.length) {
                                break;
                            }
                            payloadLength = (ndefData[ndefOffset] << 24) |
                                           (ndefData[ndefOffset + 1] << 16) |
                                           (ndefData[ndefOffset + 2] << 8) |
                                           ndefData[ndefOffset + 3];
                            ndefOffset += 4;
                        }

                        // Read ID length if present
                        let idLength = 0;
                        if (il === 1) {
                            idLength = ndefData[ndefOffset++];
                        }

                        // Read type
                        const typeBytes = ndefData.slice(ndefOffset, ndefOffset + typeLength);
                        ndefOffset += typeLength;

                        // Skip ID if present
                        if (idLength > 0) {
                            ndefOffset += idLength;
                        }

                        // Check if it's a MIME type record with our media type
                        if (tnf === 0x02) {
                            const recordMediaType = new TextDecoder().decode(typeBytes);

                            if (recordMediaType === mediaType) {
                                // Extract payload
                                const payload = ndefData.slice(ndefOffset, ndefOffset + payloadLength);
                                return payload;
                            }
                        }

                        // Skip payload and continue to next record
                        ndefOffset += payloadLength;
                    }

                    // No matching record found in this NDEF message
                    return null;
                }

                // Skip this TLV and continue searching
                offset += tlvLength;
            }

            return null;
        } catch (e) {
            console.error('Error deserializing NDEF:', e);
            return null;
        }
    }

    return {
        serialize,
        deserialize
    };
})();
