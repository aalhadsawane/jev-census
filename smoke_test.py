"""
Contract test for Jev against the direct TypeSafe API: noul (with and
without criteria), choice, score, a mixed-type batch on one state, and
structured (object) state. Uses the official typesafe-sdk, reading
TYPESAFE_API_KEY from .env.local.
"""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

results = []


def run(name, fn):
    print(f"\n=== {name} ===")
    try:
        r = fn()
        payload = r.model_dump() if hasattr(r, "model_dump") else r
        print(json.dumps(payload, indent=2, default=str))
        results.append({"name": name, "ok": True, "result": payload})
    except Exception as e:  # noqa: BLE001
        print("FAILED:", repr(e))
        results.append({"name": name, "ok": False, "error": repr(e)})
    time.sleep(2)


def main():
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY not set in .env.local")

    client = TypeSafeClient(model="jev-latest")

    run(
        "noul (no criteria)",
        lambda: client.system_one(
            "The support agent issued a full refund to the customer.",
            questions={"refunded": Noul(instructions="Was a refund issued?")},
        ),
    )

    run(
        "noul (with criteria)",
        lambda: client.system_one(
            "The build failed with exit code 1.",
            questions={
                "passed": Noul(
                    instructions="Did the build succeed?",
                    criteria={"true": "exit code 0", "false": "any non-zero exit code"},
                )
            },
        ),
    )

    run(
        "choice",
        lambda: client.system_one(
            "My card was charged twice for one order.",
            questions={
                "route": Choice(
                    instructions="Route this support ticket.",
                    criteria={
                        "billing": "payment or charge problems",
                        "shipping": "delivery problems",
                        "technical": "application bugs",
                    },
                )
            },
        ),
    )

    run(
        "score",
        lambda: client.system_one(
            "The PR adds tests, updates docs, and has a clear description.",
            questions={
                "quality": Score(
                    instructions="Rate the quality of this pull request.",
                    criteria=[
                        "poor: no tests or docs",
                        "fair: partial coverage",
                        "good: tests and docs",
                        "excellent: tests, docs, and clear rationale",
                    ],
                )
            },
        ),
    )

    run(
        "multi-question batch (noul x2 + score, one state)",
        lambda: client.system_one(
            "I cannot log in, and I also want a refund for last month.",
            questions={
                "authIssue": Noul(instructions="Is there a login problem?"),
                "wantsRefund": Noul(instructions="Is a refund requested?"),
                "urgency": Score(
                    instructions="How urgent is this ticket?",
                    criteria=["low", "medium", "high"],
                ),
            },
        ),
    )

    run(
        "structured (object) state",
        lambda: client.system_one(
            {"order": {"id": "A-1", "total": 42.5, "status": "refunded"}, "agent": "bot-7"},
            questions={"refunded": Noul(instructions="Is the order refunded?")},
        ),
    )

    client.close()

    Path("results.json").write_text(json.dumps(results, indent=2, default=str))
    ok = sum(1 for r in results if r["ok"])
    print(f"\n=== summary: {ok}/{len(results)} succeeded, written to results.json ===")


if __name__ == "__main__":
    main()
