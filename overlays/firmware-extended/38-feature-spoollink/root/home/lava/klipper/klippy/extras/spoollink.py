import logging

from . import filament_protocol

class SpoolLink:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.printer.lookup_object('webhooks').register_endpoint(
            'spoollink/set', self._handle_set)

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
