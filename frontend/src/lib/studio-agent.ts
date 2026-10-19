import type { AgAiAgentRunner } from "ag-studio";
import { answerStudioQuestion, type StudioSnapshot } from "./studio-data";

/** Documented Studio custom runner. No adapters, network, tools, or persistence. */
export function studioAgent(snapshot: StudioSnapshot): AgAiAgentRunner {
  return {
    id: "scopeshield-read-only", name: "Offline project queries (not live AI)",
    description: "Four deterministic queries over the authorized snapshot. No mutations.",
    tools: () => [],
    run: async function* (input, ctx) {
      if (ctx.signal?.aborted) return;
      yield { type: "RUN_STARTED", threadId: input.threadId, runId: input.runId };
      const latest = [...input.messages].reverse().find((m) => m.role === "user");
      const messageId = `answer-${input.runId}`;
      yield { type: "TEXT_MESSAGE_START", messageId, role: "assistant" };
      yield { type: "TEXT_MESSAGE_CONTENT", messageId, delta: answerStudioQuestion(snapshot, latest?.content || "") };
      yield { type: "TEXT_MESSAGE_END", messageId };
      yield { type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId };
    },
  };
}
