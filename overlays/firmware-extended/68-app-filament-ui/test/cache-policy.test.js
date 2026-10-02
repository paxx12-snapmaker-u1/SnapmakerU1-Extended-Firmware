'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const overlayRoot = path.join(__dirname, '..', 'root');

test('Filament Manager assets bypass old browser cache entries', () => {
    const html = fs.readFileSync(path.join(
        overlayRoot,
        'usr/local/filament-ui/html/index.html'
    ), 'utf8');

    assert.match(html, /href="style\.css\?v=2"/);
    assert.match(html, /src="spoolman-utils\.js\?v=2"/);
    assert.match(html, /src="script\.js\?v=2"/);
});

test('Filament Manager responses are not stored by browsers', () => {
    const nginx = fs.readFileSync(path.join(
        overlayRoot,
        'etc/nginx/fluidd.d/filament.conf'
    ), 'utf8');

    assert.match(
        nginx,
        /add_header Cache-Control "no-store, no-cache, must-revalidate" always;/
    );
});
