import { config } from "dotenv";
config({ path: ".env.local" });
import { experimental_evaluate as evaluate } from "ai";

try {
  const r = await evaluate({
    model: "typesafe-ai/jev",
    state: "ping",
    questions: { ok: { type: "boolean", instructions: "Is this a test?" } },
  });
  console.log("SUCCESS:", JSON.stringify(r.answers));
} catch (err: any) {
  const util = await import("node:util");
  console.log(util.inspect(err, { depth: 8, colors: false }));
}
