"""Connect the foreground scheduler to the three existing page downloaders."""
from __future__ import annotations

from core import download_scheduler
from core.settings import load_settings

_settings_provider = load_settings
_adapters = None


def configure(enabled: bool, settings_provider=None) -> None:
    global _settings_provider, _adapters
    if settings_provider is not None:
        _settings_provider = settings_provider
    if _adapters is None:
        from core import manhwa_queue
        from webui.pages import manga_get, video_get

        def manhwa(record):
            settings = _settings_provider()
            return manhwa_queue._job(record, lambda say, stop: manhwa_queue.work(record, settings, say, stop),
                                     scheduled=True)

        _adapters = {
            "manga": download_scheduler.Adapter(manga_get.GET,
                                                 lambda record: manga_get.restore_scheduled(record, _settings_provider())),
            "manhwa": download_scheduler.Adapter(manhwa_queue.TASK, manhwa, manhwa_queue.legacy_busy),
            "video": download_scheduler.Adapter(video_get.GET,
                                                 lambda record: video_get.restore_scheduled(record, _settings_provider()),
                                                 video_get.legacy_queue_busy),
        }
    download_scheduler.configure(enabled, _adapters)
