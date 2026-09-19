// Connectivity + contract test for Jev via Vercel AI Gateway's evaluation
// modality. Confirms experimental_evaluate() actually reaches typesafe-ai/jev
// and records the real response shapes for docs/00-JEV-API.md.
import { config } from "dotenv";
config({ path: ".env.local" });

import { experimental_evaluate as evaluate } from "ai";

function section(title: string) {
  console.log("\n=== " + title + " ===");
}

async function main() {
  if (!process.env.AI_GATEWAY_API_KEY) {
    throw new Error("AI_GATEWAY_API_KEY not set in .env.local");
  }

  // 1. Boolean, single question, no criteria.
  section("boolean (no criteria)");
  const r1 = await evaluate({
    model: "typesafe-ai/jev",
    state: "The support agent issued a full refund to the customer.",
    questions: {
      refunded: { type: "boolean", instructions: "Was a refund issued?" },
    },
  });
  console.log(JSON.stringify(r1, null, 2));

  // 2. Boolean, with criteria — docs example, known expected answer ~0.01.
  section("boolean (with criteria)");
  const r2 = await evaluate({
    model: "typesafe-ai/jev",
    state: "The build failed with exit code 1.",
    questions: {
      passed: {
        type: "boolean",
        instructions: "Did the build succeed?",
        criteria: { true: "exit code 0", false: "any non-zero exit code" },
      },
    },
  });
  console.log(JSON.stringify(r2, null, 2));

  // 3. Choice.
  section("choice");
  const r3 = await evaluate({
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
  });
  console.log(JSON.stringify(r3, null, 2));

  // 4. Score.
  section("score");
  const r4 = await evaluate({
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
  });
  console.log(JSON.stringify(r4, null, 2));

  // 5. Multiple mixed-type questions, one state, one round trip — the
  // batching claim the whole cost model depends on.
  section("multi-question batch (boolean x2 + score)");
  const r5 = await evaluate({
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
  });
  console.log(JSON.stringify(r5, null, 2));

  // 6. Structured (object) state.
  section("structured state");
  const r6 = await evaluate({
    model: "typesafe-ai/jev",
    state: { order: { id: "A-1", total: 42.5, status: "refunded" }, agent: "bot-7" },
    questions: {
      refunded: { type: "boolean", instructions: "Is the order refunded?" },
    },
  });
  console.log(JSON.stringify(r6, null, 2));

  section("done");
}

main().catch((err) => {
  console.error("jev-eval failed:", err);
  process.exit(1);
});
