import uuid
import json
import logging
import gcode

class Spoolman:
    def __init__(self, printer, logs):
        self.printer = printer
        self.logs = logs
        self.gcode = self.printer.lookup_object("gcode")
        self.webhooks = self.printer.lookup_object("webhooks")

    def set_active_spool(self, spool_id):
        try:
            spool_id = int(spool_id)
        except Exception:
            self.logs.warn(f"Cannot set active spool to {spool_id}, the value must be a number or None")
            spool_id = None

        self.logs.verbose(f"Active spool is: {spool_id}")
        self.webhooks.call_remote_method(
            "spoolman_set_active_spool",
            spool_id=spool_id
    )

    def clear_active_spool(self):
        self.logs.verbose("Active spool cleared")
        self.set_active_spool(None)

    def lookup_spoolman(self, sku, callback):
        self.logs.verbose(f"Looking up {sku}")

        def on_spool_result(error, spools):
            self.logs.debug(f"SPOOL RESULT -> {spools}")
            callback(error, spools)

        def on_filament_result(error, filaments):
            self.logs.debug(f"FILAMENT RESULT -> {filaments}")

            if error or not filaments:
                self.logs.error(f"on_spool_result {json.dumps(error)}")
                callback(f"filaments error for sku {sku}", filaments)
                return

            filament_id = filaments[0]["id"]

            spool_request = SpoolmanRequest(self.webhooks, self.logs, on_spool_result)
            spool_request.fetch("/v1/spool", f"filament.id={filament_id}")

        filament_request = SpoolmanRequest(self.webhooks, self.logs, on_filament_result)
        filament_request.fetch("/v1/filament", f"article_number={sku}")

    def resolve_spool(self, info, callback):
        vendor = info.get("VENDOR")
        main = info.get("MAIN_TYPE")
        sub = info.get("SUB_TYPE")
        colour = info.get("ARGB_COLOR")
        sku = info.get("SKU")
        spool_id = info.get("SPOOL_ID")

        self.logs.verbose(f"Resolving spool id for filament data: vendor->{vendor}, main_type->{main}, sub_type->{sub}, colour->{colour}, sku->{sku}, spool_id->{spool_id}")

        if not sku and not spool_id:
            self.logs.warn(f"Missing SKU, cannot resolve spool via spoolamn API.")
            callback(None)
            return

        if spool_id:
            self.logs.debug(f"Spool has already a spool_id ({spool_id}), no need to look it up")
            callback(spool_id)
            return

        def on_lookup_spoolman(error, spools):
            if error:
                self.logs.error(f"Cannot find spools for sku: {sku}")
                callback(None)
                return

            self.logs.debug(f"Spools for sku->{sku}: {spools}")

            spool = spools[0]
            callback(spool)

        self.lookup_spoolman(sku, on_lookup_spoolman)

class SpoolmanRequest:
    _pending = {}  # request_id -> instance

    def __init__(self, webhooks, logs, callback):
        self.webhooks = webhooks
        self.logs = logs
        self.callback = callback
        self.request_id = uuid.uuid4().hex

        self.reactor = webhooks.printer.get_reactor()
        self._retry_args = None
        self._endpoint_registered = False
        self._retry_count = 0
        self._max_retries = 10

    def fetch(self, path, query, method="GET"):
        try:
            cb_endpoint = f"spoolman_helper/result/{self.request_id}"

            self.logs.debug(f"SpoolmanRequest fetch: {path}?{query} id={self.request_id}")

            # store instance for routing
            SpoolmanRequest._pending[self.request_id] = self

            def _cleanup(eventtime):
                SpoolmanRequest._pending.pop(self.request_id, None)
                return self.reactor.NEVER

            self.reactor.register_timer(_cleanup, self.reactor.monotonic() + 30)

            # IMPORTANT: register endpoint only once (Klipper errors if you register same path twice)
            if not self._endpoint_registered:
                self.webhooks.register_endpoint(cb_endpoint, SpoolmanRequest._dispatch)
                self._endpoint_registered = True

            self.webhooks.call_remote_method(
                "spoolman_proxy",
                cb_endpoint=cb_endpoint,
                request_method=method,
                path=path,
                query=query
            )

        except gcode.CommandError as e:
            # Moonraker not connected yet -> retry later
            if "not registered" in str(e):
                self._retry_args = (path, query, method)

                self._retry_count += 1
                if self._retry_count > self._max_retries:
                    self.logs.error(f"spoolman_proxy unavailable after {self._retry_count} attempts id={self.request_id}")
                    return

                delay = min(5.0, 1.0 * self._retry_count)
                self.logs.debug(f"spoolman_proxy not ready, retry {self._retry_count} in {delay}s id={self.request_id}")
                self.reactor.register_timer(self._retry_fetch, self.reactor.monotonic() + delay)
                return
            raise

        except Exception:
            logging.exception("fetch error")

    def _retry_fetch(self, eventtime):
        if not self._retry_args:
            return self.reactor.NEVER

        path, query, method = self._retry_args
        self._retry_args = None

        self.logs.debug(f"Retrying fetch id={self.request_id}")
        self.fetch(path, query, method)

        return self.reactor.NEVER

    @staticmethod
    def _dispatch(web_request):
        logging.info(f"🧶 SH _dispatch dict: {getattr(web_request, '__dict__', None)}")
        try:
            request_id = web_request.method.split("/")[-1]
            logging.info(f"🧶 SH _dispatch {request_id}")
        except Exception:
            web_request.send({"ok": False, "error": "bad callback path"})
            return

        inst = SpoolmanRequest._pending.pop(request_id, None)
        logging.info(f"🧶 SH _dispatch calling _on_result")
        if not inst:
            web_request.send({"ok": False, "error": "unknown request"})
            return

        inst._on_result(web_request)

    def _on_result(self, web_request):
        params = web_request.params
        payload = params.get("payload")
        error = params.get("error")

        self.logs.debug(
            f"_on_result id={self.request_id} params={json.dumps(params)} payload={json.dumps(payload)} error={error}"
        )

        if self.callback:
            try:
                self.callback(error, payload)
            except Exception:
                logging.exception("SpoolmanRequest callback failed")

        web_request.send({"ok": True})
