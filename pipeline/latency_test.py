"""End-to-end latency test for the live stream (pipeline/20_live_stream must be running).

Writes a burst of client reviews to Lakebase in one transaction (on completed, not yet rated incidents),
then waits until every affected handyman's scorecard in maintops.handyman_performance has been recomputed.
Prints the time from commit to the last scorecard update, plus the stream's own latency_metrics.

Usage: python pipeline/latency_test.py [N]      (default 1000 reviews; reads LAKEBASE_PG_URL from .env)
"""

import random
import sys
import time
from pathlib import Path

import psycopg

REVIEWS = [
    (5, "Excellent work, very professional and quick."),
    (5, "Fixed everything on the first visit, highly recommended."),
    (4, "Good job, arrived a little late but friendly."),
    (4, "Solid work at a fair price."),
    (3, "Problem solved, but it took two visits."),
    (2, "Arrived very late and left a mess."),
    (1, "Did not show up on the agreed day and never called back."),
]


def _pg_url() -> str:
    for line in (Path(__file__).parent.parent / ".env").read_text().splitlines():
        if line.startswith("LAKEBASE_PG_URL="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("LAKEBASE_PG_URL not found in .env")


def main(n: int) -> None:
    rng = random.Random(42)
    with psycopg.connect(_pg_url()) as conn, conn.cursor() as cur:
        cur.execute("""SELECT id, handyman_user_id FROM maintops.incidents
                       WHERE status = 'completed' AND rating IS NULL
                       ORDER BY id LIMIT %s""", (n,))
        rows = cur.fetchall()
        if not rows:
            sys.exit("no completed, unrated incidents left")
        handymen = {h for _, h in rows}

        for incident_id, _ in rows:
            rating, text = rng.choice(REVIEWS)
            cur.execute("UPDATE maintops.incidents SET rating = %s, feedback = %s WHERE id = %s",
                        (rating, text, incident_id))
        cur.execute("SELECT clock_timestamp()::timestamp")   # same type as updated_at
        conn.commit()
        committed = time.time()
        commit_ts = cur.fetchone()[0]
        print(f"committed {len(rows)} reviews for {len(handymen)} handymen at {commit_ts:%H:%M:%S}")

        # Wait until every affected handyman's overall scorecard was recomputed after the commit
        while True:
            cur.execute("""SELECT count(*) FROM maintops.handyman_performance
                           WHERE incident_type = 'all' AND handyman_user_id = ANY(%s) AND updated_at >= %s""",
                        (list(handymen), commit_ts))
            done = cur.fetchone()[0]
            elapsed = time.time() - committed
            print(f"  {elapsed:5.1f}s  {done}/{len(handymen)} scorecards updated")
            if done >= len(handymen) or elapsed > 300:
                break
            time.sleep(2)

    verdict = "PASS" if done >= len(handymen) and elapsed < 60 else "FAIL"
    print(f"{verdict}: all scorecards updated {elapsed:.1f}s after commit (target < 60 s)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
