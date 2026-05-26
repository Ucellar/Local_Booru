
from PySide6.QtCore import QObject, Signal, Slot

class LibraryIndexWorker(QObject):
    progress = Signal(int, int)  # indexed, skipped
    done = Signal(dict)
    error = Signal(str)

    def __init__(self, settings, force=False):
        super().__init__()
        self.settings = dict(settings or {})
        self.force = bool(force)
        self._stop = False

    @Slot()
    def run(self):
        try:
            from core.database.indexer import index_library
            result = index_library(
                self.settings,
                force=self.force,
                progress=lambda indexed, skipped: self.progress.emit(indexed, skipped),
                stop_check=lambda: self._stop,
            )
            result["stopped"] = self._stop
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._stop = True
