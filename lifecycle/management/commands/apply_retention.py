import logging

from django.core.management.base import BaseCommand

from lifecycle import retention

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Anonymises and deletes data whose retention period has passed (see README)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Only report what would be done."
        )

    def handle(self, *args, dry_run=False, **options):
        result = retention.apply(dry_run=dry_run)
        summary = ", ".join(f"{name}={count}" for name, count in result.items())
        prefix = "Dry run, nothing changed: " if dry_run else ""
        logger.info("Retention applied: %s%s", prefix, summary)
        self.stdout.write(f"{prefix}{summary}")
