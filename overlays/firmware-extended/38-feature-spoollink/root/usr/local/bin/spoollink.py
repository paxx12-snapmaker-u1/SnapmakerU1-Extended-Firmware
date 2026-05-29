#!/usr/bin/env python3

import argparse
import asyncio
import aiohttp
import json
import logging
import logging.handlers
import os
import sys

LOG_FILE = "/oem/printer_data/logs/spoollink.log"
MOONRAKER_WS = "ws://localhost:7125/websocket"
RECONNECT_DELAY = 5.0

logger = logging.getLogger("spoollink")


def _setup_logging():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers = [
        logging.handlers.TimedRotatingFileHandler(
            LOG_FILE, when="midnight", backupCount=7
        ),
        logging.StreamHandler(sys.stderr),
        logging.handlers.SysLogHandler(address="/dev/log"),
    ]
    for h in handlers:
        h.setFormatter(fmt)
    logging.root.handlers = handlers
    logging.root.setLevel(logging.DEBUG)


def _quote(value: str) -> str:
    safe = value.replace("\r", "").replace("\n", "").replace('"', "'")
    return f'"{safe}"'


def _unquote(value: str) -> str:
    s = value.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _parse_card_uids(spool):
    raw = _unquote((spool.get("extra") or {}).get("card_uids") or "")
    return [u.strip().upper() for u in raw.split(",") if u.strip()]


def _parse_variant(vendor: str, filament: dict) -> str:
    variant = _unquote((filament.get("extra") or {}).get("variant") or "")
    if variant:
        return variant
    return "Basic" if vendor.lower() == "snapmaker" else ""


async def _ensure_field(session, spoolman_url, entity_type, key, name):
    list_url = f"{spoolman_url}/api/v1/field/{entity_type}"
    create_url = f"{spoolman_url}/api/v1/field/{entity_type}/{key}"
    try:
        async with session.get(list_url, raise_for_status=False) as resp:
            if resp.status != 200:
                print(f"  WARNING: could not read custom fields for {entity_type}")
                return
            fields = await resp.json()
        if any(f.get("key") == key for f in fields):
            print(f"  field {entity_type}/{key}: exists")
            return
        body = {
            "key": key,
            "name": name,
            "entity_type": entity_type,
            "field_type": "text",
            "order": 1,
            "default_value": json.dumps(""),
        }
        async with session.post(create_url, json=body, raise_for_status=False) as resp:
            if resp.status in (200, 201):
                print(f"  field {entity_type}/{key}: created")
            else:
                text = await resp.text()
                print(f"  WARNING: could not create field {entity_type}/{key}: {text}")
    except Exception as e:
        print(f"  WARNING: custom fields check failed ({entity_type}/{key}): {e}")


async def _test_connection(spoolman_url: str) -> bool:
    url = spoolman_url.rstrip("/")
    async with aiohttp.ClientSession() as session:
        print(f"Connecting to Spoolman at {url}...")
        try:
            async with session.get(
                f"{url}/api/v1/info", raise_for_status=False
            ) as resp:
                if resp.status != 200:
                    print(f"ERROR: GET /api/v1/info returned HTTP {resp.status}")
                    return False
                data = await resp.json()
        except Exception as e:
            print(f"ERROR: Cannot reach Spoolman at {url}: {e}")
            return False
        version = data.get("version")
        if not version:
            print("ERROR: Response does not appear to be from a Spoolman server.")
            return False
        print(f"Spoolman v{version} confirmed.")
        print("Checking custom fields...")
        await _ensure_field(session, url, "spool", "card_uids", "Card UIDs")
        await _ensure_field(session, url, "filament", "variant", "Variant")
        return True


class SpoolLink:
    def __init__(self, spoolman_url: str):
        self._spoolman_url = spoolman_url.rstrip("/")
        self._msg_id = 0

    def _next_id(self):
        self._msg_id += 1
        return self._msg_id

    async def run(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self._ws_loop(session)
                except Exception as e:
                    logger.warning("disconnected: %s", e)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _ws_loop(self, session):
        async with session.ws_connect(MOONRAKER_WS) as ws:
            logger.info("connected to Moonraker")
            await ws.send_json({
                "jsonrpc": "2.0",
                "method": "server.connection.identify",
                "params": {
                    "client_name": "spoollink",
                    "version": "1.0.0",
                    "type": "agent",
                    "url": "",
                },
                "id": self._next_id(),
            })
            await ws.send_json({
                "jsonrpc": "2.0",
                "method": "connection.register_remote_method",
                "params": {"method_name": "spoollink_resolve_spool"},
                "id": self._next_id(),
            })
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    asyncio.ensure_future(self._handle(session, ws, data))
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    logger.warning("WebSocket closed")
                    break

    async def _handle(self, session, ws, data):
        if data.get("method") != "spoollink_resolve_spool":
            return
        params = data.get("params", {})
        channel = params.get("channel")
        if channel is None:
            logger.error("spoollink_resolve_spool: missing channel")
            return
        asyncio.ensure_future(
            self._resolve_spool(
                session, ws, channel,
                spool_id=params.get("spool_id") or None,
                card_uid=params.get("card_uid") or None,
            )
        )

    async def _retry(self, fn, *args, retries=3, **kwargs):
        delay = 1.0
        for attempt in range(retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                if attempt == retries:
                    raise
                logger.debug("attempt %d/%d failed: %s", attempt + 1, retries, e)
                await asyncio.sleep(delay)
                delay *= 2

    async def _spoolman_get_by_id(self, session, spool_id):
        async with session.get(
            f"{self._spoolman_url}/api/v1/spool/{spool_id}", raise_for_status=False
        ) as resp:
            return await resp.json() if resp.status == 200 else None

    async def _spoolman_get_by_card(self, session, card_uid):
        async with session.get(
            f"{self._spoolman_url}/api/v1/spool?limit=1000&allow_archived=true",
            raise_for_status=False,
        ) as resp:
            spools = await resp.json() if resp.status == 200 else []
        uid_upper = card_uid.upper()
        return next((s for s in spools if uid_upper in _parse_card_uids(s)), None)

    async def _spoolman_add_card_uid(self, session, spool, card_uid):
        uid_upper = card_uid.upper()
        existing = _parse_card_uids(spool)
        if uid_upper in existing:
            return spool
        updated = existing + [uid_upper]
        encoded = json.dumps(",".join(updated))
        async with session.patch(
            f"{self._spoolman_url}/api/v1/spool/{spool['id']}",
            json={"extra": {"card_uids": encoded}},
            raise_for_status=False,
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            body = await resp.text()
            raise RuntimeError(f"HTTP {resp.status}: {body}")

    async def _resolve_spool(self, session, ws, channel, spool_id=None, card_uid=None):
        spool_by_id = None
        spool_by_card = None

        if spool_id is not None:
            try:
                spool_by_id = await self._retry(self._spoolman_get_by_id, session, spool_id)
            except Exception as e:
                logger.error("ch%d: fetch spool %s failed: %s", channel, spool_id, e)

        if card_uid is not None:
            try:
                spool_by_card = await self._retry(self._spoolman_get_by_card, session, card_uid)
            except Exception as e:
                logger.error("ch%d: fetch by card failed: %s", channel, e)

        spool = spool_by_id or spool_by_card
        if spool is None:
            logger.debug("ch%d: no spool resolved (spool_id=%s card=%s)", channel, spool_id, card_uid)
            return

        if card_uid is not None and spool_by_id is not None:
            if card_uid.upper() not in _parse_card_uids(spool_by_id):
                try:
                    spool = await self._retry(
                        self._spoolman_add_card_uid, session, spool_by_id, card_uid
                    )
                    logger.info("ch%d: bound spool %s to card %s", channel, spool_by_id["id"], card_uid)
                except Exception as e:
                    logger.error("ch%d: bind spool %s failed: %s", channel, spool_by_id["id"], e)

        await self._apply_spool(ws, channel, spool, card_uid or "")

    async def _apply_spool(self, ws, channel, spool, uid_hex):
        spool_id = spool.get("id", 0)
        filament = spool.get("filament", {})
        material = filament.get("material", "PLA")
        vendor = (filament.get("vendor") or {}).get("name", "Generic")
        variant = _parse_variant(vendor, filament)
        color_hex = (filament.get("color_hex") or "FFFFFF")[:6]
        script = (
            f"SET_PRINT_FILAMENT_CONFIG"
            f" CONFIG_EXTRUDER={channel}"
            f" VENDOR={_quote(vendor)}"
            f" FILAMENT_TYPE={_quote(material)}"
            f" FILAMENT_SUBTYPE={_quote(variant)}"
            f" FILAMENT_COLOR_RGBA={color_hex}FF"
            f" FILAMENT_SPOOL_ID={spool_id}"
            f" FORCE=1"
        )
        try:
            await ws.send_json({
                "jsonrpc": "2.0",
                "method": "printer.gcode.script",
                "params": {"script": script},
                "id": self._next_id(),
            })
        except Exception as e:
            logger.error("ch%d: failed to set filament: %s", channel, e)
            return
        logger.info("ch%d: applied spool %s (card %s)", channel, spool_id, uid_hex)


def main():
    parser = argparse.ArgumentParser(prog="spoollink")
    parser.add_argument("url", metavar="spoolman-url", help="Spoolman base URL")
    parser.add_argument("--test", action="store_true", help="Validate connectivity and exit")
    args = parser.parse_args()

    if args.test:
        sys.exit(0 if asyncio.run(_test_connection(args.url)) else 1)

    _setup_logging()
    logger.info("spoollink starting (spoolman: %s)", args.url)
    try:
        asyncio.run(SpoolLink(args.url).run())
    except KeyboardInterrupt:
        logger.info("spoollink stopped")


if __name__ == "__main__":
    main()
