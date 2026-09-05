import os

class AceProtocol(object):
    NAME = ''
    DEFAULT_BAUD = 0
    SERIAL_KWARGS = {}

    @staticmethod
    def _read_usb_ids(real_dev):
        """Return (vendor, product) lowercase-hex for a tty device name, or
        (None, None). Walks UP from /sys/class/tty/<dev>/device until
        idVendor/idProduct are found: they live on the USB *device* node,
        which is ONE level up for CDC-ACM (ttyACM, the genuine ACE cable) but
        TWO levels up for usb-serial adapters (ttyUSB: CH340/FTDI/CP210x
        insert an extra port node). A fixed 'device/..' only matched ttyACM,
        so a generic USB-RS485 adapter was silently dropped before the
        vid:pid check ran."""
        try:
            node = os.path.realpath('/sys/class/tty/%s/device' % real_dev)
        except OSError:
            return None, None
        for _ in range(8):
            try:
                vp = os.path.join(node, 'idVendor')
                pp = os.path.join(node, 'idProduct')
                if os.path.exists(vp) and os.path.exists(pp):
                    with open(vp, 'r') as f:
                        vendor = f.read().strip().lower()
                    with open(pp, 'r') as f:
                        product = f.read().strip().lower()
                    return vendor, product
            except (IOError, OSError):
                pass
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
        return None, None

    @classmethod
    def discover(cls):
        raise NotImplementedError

    @classmethod
    def open_transport(cls, path, baud, **kwargs):

        import serial
        merged = dict(cls.SERIAL_KWARGS)
        merged.update(kwargs)
        return serial.Serial(port=path, baudrate=baud, **merged)

    def encode_request(self, request, next_id=None):
        raise NotImplementedError

    def decode_frames(self, buffer):
        raise NotImplementedError

    def initial_handshake_requests(self):
        return [{'method': 'get_info'}]

    def make_default_info(self):
        raise NotImplementedError
