"""Reserved package for dedicated worker types.

Generic QThread/RetryQueue experiments were removed; maintenance background
operations use core.task_manager.TaskManager, while the live parser keeps its
own stoppable per-site conveyor worker.
"""
