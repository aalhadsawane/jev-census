// Connectivity spike: verify Vercel AI Gateway works before wiring up Jev.
//
// When AI_GATEWAY_API_KEY is set, the `ai` package resolves a plain
// "creator/model" string (no provider SDK import needed) through the
// Vercel AI Gateway automatically.
import { config } from "dotenv";
import { generateText } from "ai";

// dotenv/config only auto-loads .env; the key lives in .env.local.
config({ path: ".env.local" });

async function main() {
  if (!process.env.AI_GATEWAY_API_KEY) {
    throw new Error(
      "AI_GATEWAY_API_KEY is not set. Add it to .env.local before running this script."
    );
  }

  const { text } = await generateText({
    model: "openai/gpt-5.5",
    prompt:
      "Invent a new holiday. Give it a name, a date, and describe its traditions.",
  });

  console.log(text);
}

main().catch((err) => {
  console.error("Gateway spike failed:", err);
  process.exit(1);
});
