"""Back up memory spaces and the accounts database, verify the result, drop old backups.

    python scripts/backup.py /backups --keep 14          # run from cron, once a day
    python scripts/backup.py --verify /backups/voicemem-20261009T010000Z

Restore (stop the app first):
    memory:    copy <backup>/memory/* into VOICEMEM_MEMORYSPACE_ROOT
    sqlite:    copy <backup>/accounts.db to the path in DATABASE_URL
    postgres:  psql "$DATABASE_URL" < <backup>/accounts.sql   (into an empty database)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from production.accounts import database_url  # noqa: E402
from production.backup import make_backup, prune, verify_backup  # noqa: E402
from voicemem.utils.common.space import root  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="folder to write backups into, or a backup to --verify")
    ap.add_argument("--keep", type=int, default=14, help="how many backups to keep (default 14)")
    ap.add_argument("--verify", action="store_true", help="only check an existing backup")
    args = ap.parse_args()

    if args.verify:
        problems = verify_backup(args.target)
        print("\n".join(problems) or "backup OK")
        return 1 if problems else 0

    dest = make_backup(args.target, root(), database_url())
    problems = verify_backup(dest)
    if problems:
        print(f"BACKUP IS BAD ({dest}):\n" + "\n".join(problems), file=sys.stderr)
        return 1
    removed = prune(args.target, args.keep)
    print(f"backup OK: {dest} (removed {len(removed)} old)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
