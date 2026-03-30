import logging
import copy
from . import filament_protocol

SPOOLMAN_PROXY_ENDPOINT_BASE = "klippy/spoolman_proxy"


def _parse_lot_nr_card_uids(lot_nr):
    if not lot_nr:
        return []
    parts = lot_nr.split(',')
    result = []
    for part in parts:
        part = part.strip()
        if part.startswith('card_uid:'):
            result.append(part[9:])
    return result


def _has_card_uid_in_lot_nr(lot_nr, card_uid):
    return card_uid in _parse_lot_nr_card_uids(lot_nr)


def _build_lot_nr(card_uids):
    if not card_uids:
        return None
    return ",".join(f"card_uid:{uid}" for uid in card_uids)


def _add_card_uid_to_lot_nr(lot_nr, card_uid):
    existing = _parse_lot_nr_card_uids(lot_nr)
    if card_uid in existing:
        return lot_nr
    existing.append(card_uid)
    return _build_lot_nr(existing)


def _remove_card_uid_from_lot_nr(lot_nr, card_uid):
    existing = _parse_lot_nr_card_uids(lot_nr)
    if card_uid not in existing:
        return lot_nr
    existing.remove(card_uid)
    return _build_lot_nr(existing)

class AFCLaneSpoolman:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.webhooks = self.printer.lookup_object('webhooks')
        self.name = config.get_name().replace("AFC_lane_spoolman ", "", 1)
        self.lane_name = config.get("lane")
        self.lane = None
        self.filament_detect = None

        self.pending_refresh = False
        self.pending_spool_id = None
        self.pending_card_uid = None

        self._last_card_uid = None
        self._pending_lot_nr_card_uid = None

        self.printer.register_event_handler("klippy:connect", self._handle_connect)

        self.gcode.register_mux_command(
            "SET_SPOOL_ID", "LANE", self.lane_name,
            self.cmd_SET_SPOOL_ID,
            desc=self.cmd_SET_SPOOL_ID_help)

        self.gcode.register_mux_command(
            "REFRESH_SPOOL", "LANE", self.lane_name,
            self.cmd_REFRESH_SPOOL,
            desc=self.cmd_REFRESH_SPOOL_help)

        cb_base = f"{SPOOLMAN_PROXY_ENDPOINT_BASE}/{config.get_name()}"

        self.find_by_spool_id_callback = f"{cb_base}/find_by_spool_id"
        self.webhooks.register_endpoint(
            self.find_by_spool_id_callback,
            self._handle_find_by_spool_id_callback)

        self.find_by_lot_nr_callback = f"{cb_base}/find_by_lot_nr"
        self.webhooks.register_endpoint(
            self.find_by_lot_nr_callback,
            self._handle_find_by_lot_nr_callback)

        self.add_lot_nr_callback = f"{cb_base}/add_lot_nr"
        self.webhooks.register_endpoint(
            self.add_lot_nr_callback,
            self._handle_add_lot_nr_callback)

        self.remove_lot_nr_callback = f"{cb_base}/remove_lot_nr"
        self.webhooks.register_endpoint(
            self.remove_lot_nr_callback,
            self._handle_remove_lot_nr_callback)

        self.by_lot_nr_callback = f"{cb_base}/by_lot_nr"
        self.webhooks.register_endpoint(
            self.by_lot_nr_callback,
            self._handle_by_lot_nr_callback)

    def _handle_connect(self):
        try:
            self.lane = self.printer.lookup_object(f"AFC_lane {self.lane_name}")
        except Exception as e:
            logging.error(f"AFC_lane_spoolman {self.name}: lane {self.lane_name} not found: {e}")

        try:
            self.filament_detect = self.printer.lookup_object('filament_detect')
        except:
            pass

        reactor = self.printer.get_reactor()
        reactor.register_timer(self._check_card_state, reactor.NOW)

    def _get_card_uid_hex(self):
        if not self.filament_detect or not self.lane:
            return None
        try:
            all_info = self.filament_detect.get_all_filament_info()
            if self.lane.lane_index >= len(all_info):
                return None
            info = all_info[self.lane.lane_index]
            if not info.get('OFFICIAL', False):
                return None
            card_uid = info.get('CARD_UID', [])
            if not card_uid:
                return None
            return "".join(f"{b:02x}" for b in card_uid)
        except Exception as e:
            logging.error(f"AFC_lane_spoolman {self.name}: failed to get card_uid: {e}")
            return None

    def _set_filament_config(self, vendor='NONE', type='NONE', sub_type='NONE', color='FFFFFFFF', official=False, spool_id=0, weight=1000):
        if not self.lane:
            return False
        try:
            info = copy.deepcopy(filament_protocol.FILAMENT_INFO_STRUCT)
            if vendor:
                info['VENDOR'] = vendor
            if type:
                info['MAIN_TYPE'] = type
            if sub_type:
                info['SUB_TYPE'] = sub_type
            if color:
                info['COLOR_NUMS'] = 1
                info['RGB_1'] = int(color[:6], 16)
                info['ALPHA'] = int(color[6:8] or 'FF', 16)
                info['ARGB_COLOR'] = info['ALPHA'] << 24 | info['RGB_1']
            info['SPOOL_ID'] = spool_id
            info['OFFICIAL'] = spool_id not in (None, 0) or official
            logging.info(f"Setting filament config for lane {self.lane_name}: {info}")
            self.lane.print_task_config._rfid_filament_info_update_cb(self.lane.lane_index, info, is_clear=True)
            self.lane.cached_spool_weight = weight
            return True
        except Exception as e:
            logging.error(f"Failed to set filament config: {e}")
            return False

    def _clear_filament_config(self):
        if not self.lane:
            return False
        try:
            info = copy.deepcopy(filament_protocol.FILAMENT_INFO_STRUCT)
            logging.info(f"Clearing filament config for lane {self.lane_name}")
            self.lane.print_task_config._rfid_filament_info_update_cb(self.lane.lane_index, info, is_clear=True)
            self.lane.cached_spool_weight = -1
            return True
        except Exception as e:
            logging.error(f"Failed to clear spool data: {e}")
            return False

    def _check_card_state(self, eventtime):
        card_uid_hex = self._get_card_uid_hex()
        if card_uid_hex != self._last_card_uid:
            self._last_card_uid = card_uid_hex
            if card_uid_hex:
                self._search_by_lot_nr(card_uid_hex)
        return eventtime + 1.0

    def _search_by_lot_nr(self, card_uid_hex):
        if not self.webhooks.has_remote_method('spoolman_proxy'):
            return
        self._pending_lot_nr_card_uid = card_uid_hex
        try:
            self.webhooks.call_remote_method(
                "spoolman_proxy",
                cb_endpoint=self.by_lot_nr_callback,
                request_method="GET",
                path="/v1/spool",
                query=f"lot_nr=card_uid:{card_uid_hex}"
            )
        except Exception as e:
            logging.error(f"AFC_lane_spoolman {self.name}: failed to search by lot_nr: {e}")

    cmd_SET_SPOOL_ID_help = "Set spool ID and fetch filament data from Spoolman"
    def cmd_SET_SPOOL_ID(self, gcmd):
        spool_id = gcmd.get_int('SPOOL_ID', 0)
        card_uid = self._get_card_uid_hex()

        if not spool_id:
            gcmd.respond_info(f"Clearing spool data for lane {self.lane_name}")
            reactor = self.printer.get_reactor()
            reactor.register_callback(lambda _: self._clear_filament_config())
            self.pending_card_uid = None
            return

        self.pending_refresh = False
        self.pending_spool_id = spool_id
        self.pending_card_uid = card_uid

        gcmd.respond_info(f"Fetching spool {spool_id} data for lane {self.lane_name}...")

        try:
            self.webhooks.call_remote_method(
                "spoolman_proxy",
                cb_endpoint=self.find_by_spool_id_callback,
                request_method="GET",
                path=f"/v1/spool/{spool_id}"
            )
        except Exception as e:
            logging.error(f"Failed to query spoolman: {e}")

        if card_uid:
            try:
                self.webhooks.call_remote_method(
                    "spoolman_proxy",
                    cb_endpoint=self.find_by_lot_nr_callback,
                    request_method="GET",
                    path="/v1/spool",
                    query=f"lot_nr=card_uid:{card_uid}"
                )
            except Exception as e:
                logging.error(f"Failed to query spoolman by lot_nr: {e}")

    def _refresh_spool(self, spool_id):
        if not spool_id or not self.webhooks.has_remote_method('spoolman_proxy'):
            return
        try:
            self.pending_refresh = True
            self.pending_spool_id = spool_id
            self.webhooks.call_remote_method(
                "spoolman_proxy",
                cb_endpoint=self.find_by_spool_id_callback,
                request_method="GET",
                path=f"/v1/spool/{spool_id}"
            )
        except Exception as e:
            logging.error(f"Failed to query spoolman: {e}")

    cmd_REFRESH_SPOOL_help = "Refresh current spool data from Spoolman"
    def cmd_REFRESH_SPOOL(self, gcmd):
        if not self.lane:
            return
        state = self.lane._get_state()
        spool_id = state.get('spool', {}).get('spool_id', 0)
        if not spool_id:
            gcmd.respond_info(f"No spool configured for lane {self.lane_name}")
            return
        self._refresh_spool(spool_id)

    def _handle_find_by_spool_id_callback(self, web_request):
        try:
            payload = web_request.get_dict('payload', {})
            error = web_request.get('error', None)

            if error:
                logging.error(f"Spoolman error: {error}")
                return

            self._apply_spool_data(payload)
        except Exception as e:
            raise web_request.error(f"Failed to process spoolman response: {e}")

    def _set_spool_from_response(self, response, card_uid=None):
        if not response or not self.lane:
            return

        spool_id = response.get('id', 0)
        filament = response.get('filament', {})
        material = filament.get('material', 'PLA')
        vendor = filament.get('vendor', {}).get('name', 'Generic')
        color_hex = filament.get('color_hex', 'FFFFFFFF')
        sub_type = filament.get('extra', {}).get('sub_type', 'Basic')
        weight = response.get('remaining_weight', -1)
        lot_nr = response.get('lot_nr', None)

        self._set_filament_config(
            vendor=vendor,
            type=material,
            sub_type=sub_type,
            color=color_hex,
            spool_id=spool_id,
            weight=weight,
            official=True
        )

        if card_uid and not _has_card_uid_in_lot_nr(lot_nr, card_uid):
            new_lot_nr = _add_card_uid_to_lot_nr(lot_nr, card_uid)
            try:
                self.webhooks.call_remote_method(
                    "spoolman_proxy",
                    cb_endpoint=self.add_lot_nr_callback,
                    request_method="PATCH",
                    path=f"/v1/spool/{spool_id}",
                    body={"lot_nr": new_lot_nr}
                )
            except Exception as e:
                logging.error(f"Failed to add lot_nr to spool {spool_id}: {e}")

    def _apply_spool_data(self, response):
        if not response or not self.lane:
            return

        spool_id = response.get('id', 0)
        weight = response.get('remaining_weight', -1)
        lot_nr = response.get('lot_nr', None)

        if self.pending_spool_id != spool_id:
            logging.warning(f"Spool ID mismatch for lane {self.lane_name}: expected {self.pending_spool_id}, got {spool_id}")
            return

        if self.pending_refresh:
            self.pending_refresh = False
            self.lane.cached_spool_weight = weight
            return

        self._set_spool_from_response(response, self.pending_card_uid)

    def _handle_find_by_lot_nr_callback(self, web_request):
        try:
            payload = web_request.get('payload', None)
            error = web_request.get('error', None)

            if error:
                logging.error(f"Spoolman lot_nr search error: {error}")
                return

            if not isinstance(payload, list):
                return

            card_uid = self.pending_card_uid
            if not card_uid:
                return

            for spool in payload:
                spool_id = spool.get('id', 0)
                lot_nr = spool.get('lot_nr', None)
                if spool_id and spool_id != self.pending_spool_id:
                    new_lot_nr = _remove_card_uid_from_lot_nr(lot_nr, card_uid)
                    if new_lot_nr != lot_nr:
                        try:
                            self.webhooks.call_remote_method(
                                "spoolman_proxy",
                                cb_endpoint=self.remove_lot_nr_callback,
                                request_method="PATCH",
                                path=f"/v1/spool/{spool_id}",
                                body={"lot_nr": new_lot_nr}
                            )
                        except Exception as e:
                            logging.error(f"Failed to remove lot_nr from spool {spool_id}: {e}")
        except Exception as e:
            raise web_request.error(f"Failed to process lot_nr search: {e}")

    def _handle_add_lot_nr_callback(self, web_request):
        error = web_request.get('error', None)
        if error:
            logging.error(f"Failed to add lot_nr: {error}")
            return
        payload = web_request.get_dict('payload', {})
        logging.info(f"lot_nr set for spool {payload.get('id', '?')}")

    def _handle_remove_lot_nr_callback(self, web_request):
        error = web_request.get('error', None)
        if error:
            logging.error(f"Failed to remove lot_nr: {error}")
            return
        payload = web_request.get_dict('payload', {})
        logging.info(f"lot_nr cleared from spool {payload.get('id', '?')}")

    def _handle_by_lot_nr_callback(self, web_request):
        try:
            payload = web_request.get('payload', None)
            error = web_request.get('error', None)

            if error:
                logging.error(f"AFC_lane_spoolman {self.name}: spoolman error: {error}")
                return

            if not isinstance(payload, list) or not payload:
                return

            spool = payload[0]
            spool_id = spool.get('id', 0)
            card_uid_hex = self._pending_lot_nr_card_uid

            if not spool_id or not card_uid_hex:
                return

            self._set_spool_from_response(spool, card_uid_hex)
        except Exception as e:
            raise web_request.error(f"Failed to process by_lot_nr response: {e}")

    def get_status(self, eventtime=None):
        return {
            'lane': self.lane_name,
            'last_card_uid': self._last_card_uid or '',
        }

def load_config_prefix(config):
    return AFCLaneSpoolman(config)
