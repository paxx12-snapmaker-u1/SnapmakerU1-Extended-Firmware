import base64
import logging
from . import fm175xx_reader

FILAMENT_DT_OK = 0
FILAMENT_DT_STATE_IDLE = 0

# NTAG Capability Container expected values (page 3, 4 bytes)
# Format: [NDEF magic, version, capacity, access]
NTAG215_CC = [0xE1, 0x10, 0x3F, 0x00]  # 504 bytes user capacity
NTAG216_CC = [0xE1, 0x10, 0x6D, 0x00]  # 872 bytes user capacity
VALID_CC_VALUES = (NTAG215_CC, NTAG216_CC)

class FilamentTag:
    """Klipper module for writing and erasing NTAG filament tags."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # Will be initialized in klippy:ready handler
        self.filament_detect = None
        self.fm175xx_reader = None
        self.channel_nums = 4

        # Register gcode commands
        self.gcode.register_command(
            'FILAMENT_TAG_WRITE',
            self.cmd_FILAMENT_TAG_WRITE,
            desc="Write raw NDEF data to NTAG tag"
        )
        self.gcode.register_command(
            'FILAMENT_TAG_ERASE',
            self.cmd_FILAMENT_TAG_ERASE,
            desc="Erase NTAG tag"
        )

        # Register ready handler to access filament_detect module
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_ready(self):
        """Initialize module references once Klipper is ready."""
        try:
            self.filament_detect = self.printer.lookup_object('filament_detect')
            self.fm175xx_reader = self.filament_detect._fm175xx_reader
            self.channel_nums = self.filament_detect._channel_nums
        except Exception as e:
            logging.error(f"FilamentTag: Failed to initialize: {e}")
            raise

    def _wait_for_detection(self, channel, timeout=3.0):
        """Wait for filament detection to complete (state returns to IDLE)."""
        deadline = self.reactor.monotonic() + timeout
        while self.reactor.monotonic() < deadline:
            if self.filament_detect._state[channel] == FILAMENT_DT_STATE_IDLE:
                return True
            self.reactor.pause(self.reactor.monotonic() + 0.1)
        return False

    def _ensure_cc_bytes(self, channel, info):
        """Ensure CC bytes (page 3) are correct for an NTAG tag.

        CC is OTP (One-Time Programmable): hardware performs new = old | written,
        so bits can only transition 0->1. We check whether writing the target CC
        would produce the correct result before attempting.

        Returns:
            (success: bool, warning: str or None)
            success is False if CC is unrecoverably corrupted or write failed.
        """
        current_cc = info.get('TAG_CC', [])
        if not current_cc or len(current_cc) != 4:
            return True, "CC bytes not available from tag read; skipping CC check"

        # Already correct — no write needed
        if current_cc in VALID_CC_VALUES:
            return True, None

        # Determine target CC from capacity byte (index 2)
        if current_cc[2] == 0x6D:
            target_cc = NTAG216_CC
        else:
            target_cc = NTAG215_CC

        # Simulate OTP write: result = current | target
        result_cc = [current_cc[i] | target_cc[i] for i in range(4)]

        if result_cc != target_cc:
            current_hex = ' '.join(f'{b:02X}' for b in current_cc)
            target_hex = ' '.join(f'{b:02X}' for b in target_cc)
            result_hex = ' '.join(f'{b:02X}' for b in result_cc)
            warning = (
                f"CC [{current_hex}] cannot be corrected to [{target_hex}]. "
                f"OTP write would produce [{result_hex}]. "
                f"Use a fresh tag for correct NDEF compatibility.")
            logging.warning("Channel %d: %s", channel, warning)
            return False, warning

        # Write target CC to page 3
        logging.info("Channel %d: Writing CC [%s] to page 3",
                     channel, ' '.join(f'{b:02X}' for b in target_cc))
        try:
            result = self.fm175xx_reader.write_ntag_data(
                channel, target_cc, start_page=3)
            if result != fm175xx_reader.FM175XX_OK:
                warning = f"Failed to write CC bytes: driver error {result}"
                logging.error("Channel %d: %s", channel, warning)
                return False, warning
        except Exception as e:
            warning = f"Failed to write CC bytes: {e}"
            logging.exception("Channel %d: CC write failed", channel)
            return False, warning

        logging.info("Channel %d: CC bytes written successfully", channel)
        return True, None

    def cmd_FILAMENT_TAG_WRITE(self, gcmd):
        """Write data to NTAG tag.

        Writes CC bytes (page 3) if they are blank or correctable via OTP,
        then writes user data starting at page 4. Warns if CC is corrupted
        beyond repair (OTP bits already set incorrectly).

        Required parameters:
          CHANNEL: RFID channel number (0-3)
          DATA: URL-safe base64 encoded bytes to write starting at page 4

        Example:
          FILAMENT_TAG_WRITE CHANNEL=0 DATA=AxISUBBhcHBsaWNhdGlvbi9qc29u...
        """
        # Check if print is in progress
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats and print_stats.get_status(None).get('state') == 'printing':
            raise gcmd.error("Cannot write tag while printing")

        channel = gcmd.get_int('CHANNEL', 0, minval=0, maxval=self.channel_nums-1)
        data_b64 = gcmd.get('DATA')

        if not data_b64:
            raise gcmd.error("DATA parameter is required")

        # Decode URL-safe base64 to bytes
        try:
            # Add padding if stripped
            padding = 4 - len(data_b64) % 4
            if padding != 4:
                data_b64 += '=' * padding
            data_bytes = list(base64.urlsafe_b64decode(data_b64))
        except Exception as e:
            raise gcmd.error(f"Invalid base64 DATA: {e}")

        if len(data_bytes) < 1:
            raise gcmd.error("DATA is empty")

        # Check if tag is present
        error, info = self.filament_detect.get_a_filament_info(channel)
        if error != FILAMENT_DT_OK:
            raise gcmd.error(f"Failed to get tag info for channel {channel}")

        if not info.get('CARD_UID') or len(info.get('CARD_UID', [])) == 0:
            raise gcmd.error(
                f"No RFID tag detected on channel {channel}. "
                "Please insert a tag first.")

        # Verify it's an NTAG tag (not M1)
        card_type = info.get('CARD_TYPE', '')
        if card_type == 'M1':
            raise gcmd.error(
                f"Tag on channel {channel} is M1 type. "
                "This command only supports NTAG tags.")

        # Ensure CC bytes (page 3) are correct — write if needed
        _cc_success, cc_warning = self._ensure_cc_bytes(channel, info)

        # Write user data starting at page 4
        try:
            result = self.fm175xx_reader.write_ntag_data(
                channel, data_bytes, start_page=4)
            if result != fm175xx_reader.FM175XX_OK:
                raise gcmd.error(
                    f"Failed to write tag: driver error code {result}")
        except Exception as e:
            logging.exception("Tag write failed")
            raise gcmd.error(f"Failed to write tag: {str(e)}")

        # Verify write by reading back
        self.filament_detect.request_update_filament_info(channel)
        self._wait_for_detection(channel)

        error, verify_info = self.filament_detect.get_a_filament_info(channel)
        if (error == FILAMENT_DT_OK
                and verify_info.get('MAIN_TYPE')
                and verify_info['MAIN_TYPE'] != 'NONE'):
            msg = (f"Tag written and verified successfully on channel {channel} | "
                   f"Material: {verify_info.get('VENDOR', '')} "
                   f"{verify_info.get('MAIN_TYPE', '')} | "
                   f"{len(data_bytes)} bytes written")
            if cc_warning:
                msg += f"\n{cc_warning}"
            gcmd.respond_info(msg)
        else:
            msg = (f"Tag written on channel {channel} ({len(data_bytes)} bytes) "
                   "but could not verify content. Please read tag to confirm.")
            if cc_warning:
                msg += f"\n{cc_warning}"
            gcmd.respond_info(msg)

    def cmd_FILAMENT_TAG_ERASE(self, gcmd):
        """Erase NTAG tag (clears all user data).

        Also writes CC bytes (page 3) if needed, ensuring the tag is
        NDEF-valid after erase. This is important for initializing
        fresh blank tags.

        Required parameters:
          CHANNEL: RFID channel number (0-3)
          CONFIRM: Must be set to 1 to confirm erase operation

        Example:
          FILAMENT_TAG_ERASE CHANNEL=0 CONFIRM=1
        """
        # Check if print is in progress
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats and print_stats.get_status(None).get('state') == 'printing':
            raise gcmd.error("Cannot erase tag while printing")

        channel = gcmd.get_int('CHANNEL', 0, minval=0, maxval=self.channel_nums-1)
        confirm = gcmd.get_int('CONFIRM', 0, minval=0, maxval=1)

        if confirm != 1:
            raise gcmd.error("Tag erase requires CONFIRM=1 parameter to proceed")

        # Check if tag is present
        error, info = self.filament_detect.get_a_filament_info(channel)
        if error != FILAMENT_DT_OK:
            raise gcmd.error(f"Failed to get tag info for channel {channel}")

        if not info.get('CARD_UID') or len(info.get('CARD_UID', [])) == 0:
            raise gcmd.error(f"No RFID tag detected on channel {channel}")

        # Verify it's an NTAG tag (not M1)
        card_type = info.get('CARD_TYPE', '')
        if card_type == 'M1':
            raise gcmd.error(f"Tag on channel {channel} is M1 type. Cannot erase M1 tags.")

        # Ensure CC bytes are correct (important for fresh blank tags)
        _cc_success, cc_warning = self._ensure_cc_bytes(channel, info)

        # Write empty NDEF TLV starting at page 4
        # Empty NDEF: 03 00 FE (TLV type=NDEF, length=0, terminator)
        # Pad with zeros to clear old data (124 bytes = 31 pages, pages 4-34)
        empty_ndef = [0x03, 0x00, 0xFE] + [0x00] * 121

        try:
            result = self.fm175xx_reader.write_ntag_data(
                channel, empty_ndef, start_page=4)
            if result != fm175xx_reader.FM175XX_OK:
                raise gcmd.error(f"Failed to erase tag: driver error code {result}")
        except Exception as e:
            logging.exception("Tag erase failed")
            raise gcmd.error(f"Failed to erase tag: {str(e)}")

        # Verify erase by reading back
        self.filament_detect.request_update_filament_info(channel)
        self._wait_for_detection(channel)

        error, verify_info = self.filament_detect.get_a_filament_info(channel)
        msg = None
        if error == FILAMENT_DT_OK:
            if not verify_info.get('MAIN_TYPE') or verify_info['MAIN_TYPE'] in ('', 'NONE'):
                msg = f"Tag erased successfully on channel {channel}"
            else:
                msg = f"Tag erased on channel {channel} (may require manual verification)"
        else:
            msg = f"Tag erased on channel {channel}"
        if cc_warning:
            msg += f"\n{cc_warning}"
        gcmd.respond_info(msg)

def load_config(config):
    """Load the filament_tag module."""
    return FilamentTag(config)
