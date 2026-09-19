// Connectivity + contract test for Jev via Vercel AI Gateway's evaluation
// modality. Confirms experimental_evaluate() actually reaches typesafe-ai/jev
// and records the real response shapes for docs/00-JEV-API.md.
//
// Free tier is rate-limited (observed: 429 after ~2 calls in quick succession),
// so each case runs independently with a pause between, and results are
// written to results.json regardless of partial failure.
import { writeFileSync } from "node:fs";
import { config } from "dotenv";
config({ path: ".env.local" });

import { experimental_evaluate as evaluate } from "ai";

const SPACING_MS = 8000;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type CaseResult = { name: string; ok: boolean; result?: unknown; error?: string };
const results: CaseResult[] = [];

async function run(name: string, fn: () => Promise<unknown>) {
  console.log(`\n=== ${name} ===`);
  try {
    const result = await fn();
    console.log(JSON.stringify(result, null, 2));
    results.push({ name, ok: true, result });
  } catch (err: any) {
    const msg = err?.message ?? String(err);
    console.log("FAILED:", msg);
    results.push({ name, ok: false, error: msg });
  }
  await sleep(SPACING_MS);
}

async function main() {
  if (!process.env.AI_GATEWAY_API_KEY) {
    throw new Error("AI_GATEWAY_API_KEY not set in .env.local");
  }

  await run("boolean (no criteria)", () =>
    evaluate({
      model: "typesafe-ai/jev",
      state: "The support agent issued a full refund to the customer.",
      questions: {
        refunded: { type: "boolean", instructions: "Was a refund issued?" },
      },
    })
  );

  await run("boolean (with criteria)", () =>
    evaluate({
      model: "typesafe-ai/jev",
      state: "The build failed with exit code 1.",
      questions: {
        passed: {
          type: "boolean",
          instructions: "Did the build succeed?",
          criteria: { true: "exit code 0", false: "any non-zero exit code" },
        },
      },
    })
  );

  await run("choice", () =>
    evaluate({
      model: "typesafe-ai/jev",
      state: "My card was charged twice for one order.",
      questions: {
        route: {
          type: "choice",
          instructions: "Route this support ticket.",
          criteria: {
            billing: "payment or charge problems",
            shipping: "delivery problems",
            technical: "application bugs",
          },
        },
      },
    })
  );

  await run("score", () =>
    evaluate({
      model: "typesafe-ai/jev",
      state: "The PR adds tests, updates docs, and has a clear description.",
      questions: {
        quality: {
          type: "score",
          instructions: "Rate the quality of this pull request.",
          criteria: [
            "poor: no tests or docs",
            "fair: partial coverage",
            "good: tests and docs",
            "excellent: tests, docs, and clear rationale",
          ],
        },
      },
    })
  );

  await run("multi-question batch (boolean x2 + score, one state)", () =>
    evaluate({
      model: "typesafe-ai/jev",
      state: "I cannot log in, and I also want a refund for last month.",
      questions: {
        authIssue: { type: "boolean", instructions: "Is there a login problem?" },
        wantsRefund: { type: "boolean", instructions: "Is a refund requested?" },
        urgency: {
          type: "score",
          instructions: "How urgent is this ticket?",
          criteria: ["low", "medium", "high"],
        },
      },
    })
  );

  await run("structured (object) state", () =>
    evaluate({
      model: "typesafe-ai/jev",
      state: { order: { id: "A-1", total: 42.5, status: "refunded" }, agent: "bot-7" },
      questions: {
        refunded: { type: "boolean", instructions: "Is the order refunded?" },
      },
    })
  );

  writeFileSync("results.json", JSON.stringify(results, null, 2));
  const ok = results.filter((r) => r.ok).length;
  console.log(`\n=== summary: ${ok}/${results.length} succeeded, written to results.json ===`);
}

main().catch((err) => {
  console.error("jev-eval crashed:", err);
  writeFileSync("results.json", JSON.stringify(results, null, 2));
  process.exit(1);
});
