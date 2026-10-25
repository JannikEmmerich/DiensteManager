"""The worker of the worker container: sends the queued emails and the daily summaries
and applies the retention periods once a day (see "Emails" and "Data lifecycle" in the
README).

It runs until it is stopped (SIGTERM, e.g. by "docker compose stop"). With --once it
only sends what is due and ends, e.g. for a cron job (then run apply_retention daily
yourself).
"""

import logging
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from outbox import worker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sends queued emails and daily summaries; applies the retention periods daily."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Only send what is due now, then end."
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=10,
            help="Seconds between two looks at the queue (default: 10).",
        )
        parser.add_argument(
            "--heartbeat",
            type=Path,
            help="File whose modification time shows that the worker is alive "
            "(for the healthcheck of the container).",
        )

    def handle(self, *args, once, interval, heartbeat, **options):
        self.stop = stop = threading.Event()
        if once:
            self.send()
            return
        previous = {
            signum: signal.signal(signum, lambda *_args: stop.set())
            for signum in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            self.loop(interval, heartbeat)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def loop(self, interval, heartbeat):
        next_retention = timezone.now()
        self.stdout.write("Worker started.")
        while not self.stop.is_set():
            # The database may have restarted or closed the connection in the meantime.
            close_old_connections()
            if timezone.now() >= next_retention:
                self.apply_retention()
                next_retention = timezone.now() + timedelta(days=1)
            try:
                self.send()
            except Exception:
                logger.exception("Could not process the email queue; trying again")
            if heartbeat:
                heartbeat.touch()
            self.stop.wait(interval)
        self.stdout.write("Worker stopped.")

    def send(self):
        summaries = worker.send_digests()
        if summaries:
            logger.info("Prepared %s daily summaries", summaries)
        # Full batches: more are waiting. After a while, the loop gets its turn again
        # (heartbeat, retention).
        started = time.monotonic()
        while (
            worker.send_due() == worker.BATCH_SIZE
            and not self.stop.is_set()
            and time.monotonic() - started < 300
        ):
            pass

    def apply_retention(self):
        try:
            call_command("apply_retention", stdout=self.stdout)
        except Exception:
            logger.exception("apply_retention failed; trying again tomorrow")
