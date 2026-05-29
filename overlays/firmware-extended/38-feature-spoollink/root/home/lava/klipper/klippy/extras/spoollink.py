import logging

from . import filament_protocol

class SpoolLink:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.printer.lookup_object('webhooks').register_endpoint(
            'spoollink/set', self._handle_set)
        self.printer.register_event_handler('klippy:ready', self._handle_ready)

    def _handle_ready(self):
        fd = self.printer.lookup_object('filament_detect', None)
        if fd is not None:
            fd.register_cb_2_update_filament_info(self._on_filament_info)

    def _on_filament_info(self, channel, info, is_clear):
        if is_clear:
            return
        spool_id = info.get('SPOOL_ID', 0) or 0
        uid_raw = info.get('CARD_UID') or []
        card_uid = (''.join('%02X' % b for b in uid_raw)
                    if uid_raw and any(b != 0 for b in uid_raw) else '')
        wh = self.printer.lookup_object('webhooks')
        if spool_id != 0 or not wh.has_remote_method('spoollink_resolve_spool'):
            return
        if card_uid:
            wh.call_remote_method('spoollink_resolve_spool',
                                  channel=channel, spool_id=spool_id,
                                  card_uid=card_uid)
        else:
            self.gcode.respond_raw('// SpoolLink: E%d no card present' % (channel + 1))

    def _handle_set(self, web_request):
        wh = self.printer.lookup_object('webhooks')
        if not wh.has_remote_method('spoollink_resolve_spool'):
            raise web_request.error('spoollink not supported')

        channel = web_request.get_int('channel')
        message = web_request.get_str('message', '')
        status = web_request.get_str('status', 'ok')

        if status == 'error':
            logging.warning('[spoollink] ch%d error: %s', channel, message)
            self.gcode.respond_raw('!! %s' % message)
            web_request.send({})
            return

        info = dict(filament_protocol.FILAMENT_INFO_STRUCT)
        info.update(web_request.get('info', {}))
        if message:
            logging.info('[spoollink] ch%d: %s', channel, message)
        ptc = self.printer.lookup_object('print_task_config')
        ptc._rfid_filament_info_update_cb(channel, info, is_clear=True)
        self.gcode.respond_raw('// %s' % message)
        web_request.send({})

def load_config(config):
    return SpoolLink(config)
