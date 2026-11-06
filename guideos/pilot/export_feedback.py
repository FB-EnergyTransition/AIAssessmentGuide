from __future__ import annotations

import argparse
import csv
import os
import sys

from guideos.pilot.feedback import PostgresPilotFeedbackRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Export GuideOS pilot feedback as CSV.")
    parser.add_argument("--database-url", default=os.environ.get("GUIDEOS_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or GUIDEOS_DATABASE_URL is required")
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=["session_id", "domain_id", "domain_version", "rating", "comment", "created_at"],
    )
    writer.writeheader()
    writer.writerows(PostgresPilotFeedbackRepository(args.database_url).export_rows())


if __name__ == "__main__":
    main()
