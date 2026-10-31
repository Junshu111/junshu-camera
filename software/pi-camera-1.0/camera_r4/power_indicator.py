"""Optional stage indicator; no pin or hardware driver is selected implicitly."""
import logging

log = logging.getLogger(__name__)


class OptionalIndicator:
    def __init__(self, enabled=False, driver=None):
        self.enabled = bool(enabled)
        self.driver = driver
        self.available = self.enabled and driver is not None
        self.reason = '' if self.available else ('driver_not_verified' if enabled else 'disabled')

    def stage(self, name):
        if not self.available:
            return False
        try:
            self.driver.stage(name)
            return True
        except Exception as error:
            self.available = False
            self.reason = str(error)
            log.warning('Optional power indicator unavailable: %s', error)
            return False
