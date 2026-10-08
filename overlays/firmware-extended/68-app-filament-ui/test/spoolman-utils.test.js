'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const SM = require('../root/usr/local/filament-ui/html/spoolman-utils.js');

function spool(id, uids, filament = {}) {
    return {
        id,
        extra: { card_uids: SM.encodeCardUids(uids) },
        filament: {
            name: `Filament ${id}`,
            material: 'PLA',
            color_hex: '112233',
            vendor: { name: 'Example' },
            ...filament,
        },
    };
}

test('normalizes U1 byte-array and text UIDs', () => {
    assert.equal(SM.normalizeUid([0xA1, 0xB2, 0xC3, 0xD4]), 'A1B2C3D4');
    assert.equal(SM.normalizeUid('a1:b2:c3:d4'), 'A1B2C3D4');
    assert.equal(SM.normalizeUid([0, 0, 0, 0]), '');
    assert.equal(SM.normalizeUid('not-a-uid'), '');
});

test('round-trips JSON-encoded, comma-separated card_uids', () => {
    const encoded = SM.encodeCardUids(['aabbccdd', '11223344', 'AABBCCDD']);
    assert.equal(encoded, '"AABBCCDD,11223344"');
    assert.deepEqual(SM.parseCardUids({ extra: { card_uids: encoded } }), [
        'AABBCCDD',
        '11223344',
    ]);
});

test('treats blank and Basic variants as the same exact match', () => {
    const filaments = [
        {
            id: 18,
            vendor: { name: 'Bambu Lab' },
            material: 'PLA',
            color_hex: '9d2235',
            extra: { variant: '""' },
        },
        {
            id: 19,
            vendor: { name: 'Bambu Lab' },
            material: 'PLA',
            color_hex: 'FFFFFF',
            extra: { variant: '"Basic"' },
        },
    ];
    const matches = SM.findMatchingFilaments(filaments, {
        vendor: 'bambu lab',
        material: 'pla',
        variant: 'Basic',
        color: '#9D2235',
    });
    assert.deepEqual(matches.map(item => item.id), [18]);
});

test('requires metadata-bearing RFID tags for spool creation', () => {
    const complete = {
        uid: [0x01, 0x02, 0x03, 0x04],
        rfid_data: { vendor: 'Bambu Lab', type: 'PLA', color: '9D2235' },
    };
    assert.equal(SM.isCreateEligible(complete), true);
    assert.equal(SM.isCreateEligible({ ...complete, rfid_data: { type: 'PLA', color: '9D2235' } }), false);
    assert.equal(SM.isCreateEligible({ ...complete, uid: [0, 0, 0, 0] }), false);
});

test('builds standard Spoolman filament and spool payloads', () => {
    const filament = SM.buildFilamentPayload({
        name: 'PLA Basic Red',
        material: 'PLA',
        variant: 'Basic',
        color: '#9d2235',
        diameter: '1.75',
        density: '1.24',
        net_weight: '1000',
        tare_weight: '250',
        nozzle_temp: '230',
        bed_temp: '55',
    }, 7);
    assert.deepEqual(filament, {
        name: 'PLA Basic Red',
        material: 'PLA',
        color_hex: '9D2235',
        diameter: 1.75,
        density: 1.24,
        vendor_id: 7,
        weight: 1000,
        spool_weight: 250,
        settings_extruder_temp: 230,
        settings_bed_temp: 55,
        extra: { variant: '"Basic"' },
    });

    const createdSpool = SM.buildSpoolPayload(18, {
        uid: 'a1b2c3d4',
        net_weight: '1000',
        tare_weight: '250',
    });
    assert.deepEqual(createdSpool, {
        filament_id: 18,
        initial_weight: 1000,
        remaining_weight: 1000,
        spool_weight: 250,
        extra: { card_uids: '"A1B2C3D4"' },
    });
});

test('classifies first, second, extra, and moved RFID assignments', () => {
    const first = SM.assignmentDecision(spool(1, []), 'AABBCCDD', []);
    const second = SM.assignmentDecision(spool(1, ['01020304']), 'AABBCCDD', []);
    const extra = SM.assignmentDecision(spool(1, ['01020304', '05060708']), 'AABBCCDD', []);
    const move = SM.assignmentDecision(
        spool(1, ['01020304']),
        'AABBCCDD',
        [spool(2, ['AABBCCDD'])],
    );
    const existing = SM.assignmentDecision(spool(1, ['AABBCCDD']), 'AABBCCDD', [spool(1, ['AABBCCDD'])]);

    assert.equal(first.action, 'add_first');
    assert.equal(first.requiresConfirmation, false);
    assert.equal(second.action, 'add_second');
    assert.equal(second.confirmLabel, 'Add Second RFID');
    assert.equal(extra.action, 'add_extra');
    assert.equal(extra.confirmLabel, 'Add Another RFID');
    assert.equal(move.action, 'move');
    assert.equal(move.confirmLabel, 'Move RFID');
    assert.equal(existing.action, 'already_assigned');
    assert.equal(existing.requiresConfirmation, false);
});

test('verifies exclusive ownership while preserving both existing target UIDs', () => {
    const prior = ['01020304', '05060708'];
    const success = SM.verifyUidAssignment([
        spool(1, [...prior, 'AABBCCDD']),
        spool(2, []),
    ], 1, 'AABBCCDD', prior);
    assert.equal(success.ok, true);

    const duplicate = SM.verifyUidAssignment([
        spool(1, [...prior, 'AABBCCDD']),
        spool(2, ['AABBCCDD']),
    ], 1, 'AABBCCDD', prior);
    assert.equal(duplicate.ok, false);
    assert.deepEqual(duplicate.ownerIds, [1, 2]);

    const lostPriorUid = SM.verifyUidAssignment([
        spool(1, ['01020304', 'AABBCCDD']),
    ], 1, 'AABBCCDD', prior);
    assert.equal(lostPriorUid.ok, false);
    assert.deepEqual(lostPriorUid.missingPrevious, ['05060708']);
});

test('uses material density fallback and an editable friendly name', () => {
    assert.equal(SM.materialDensity('PETG', 0), 1.27);
    assert.equal(SM.defaultFilamentName({ material: 'PLA', variant: 'Basic', color: '9D2235' }), 'PLA Basic Red');
});
