import os

import httpx


API_BASE_URL = os.getenv("CARD_TRACKER_API_URL", "http://localhost:8000")


def main() -> None:
    response = httpx.post(
        f"{API_BASE_URL}/api/refresh-all",
        timeout=600.0,
    )
    response.raise_for_status()

    data = response.json()
    print(f"Attempted: {data['attempted']}")
    print(
        "Skipped due to daily safety limit: "
        f"{data['skipped_due_to_daily_safety_limit']}"
    )

    failures = [row for row in data["results"] if not row["ok"]]
    print(f"Failures: {len(failures)}")

    for failure in failures:
        print(
            f"- {failure['card_id']}: "
            f"{failure.get('error', 'Unknown error')}"
        )


if __name__ == "__main__":
    main()
