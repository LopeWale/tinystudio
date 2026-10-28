import { AssistantMessageContent } from "./messageContent";
/** Public, illustrative catalog data; these are not live evaluation scores. */
export const ASSISTANT_MARKDOWN_SAMPLE =
  '## Evaluation review (illustrative fixture)\n\n**Model A** leads on `exact` score.\n\n| Model | exact | json |\n| --- | ---: | ---: |\n| Model A | 85% | 95% |\n| Model B | 70% | 90% |\n\n- These are held-out fixture rows.\n- Check for training/validation overlap before generalizing.\n\n```json\n{"next_step":"inspect failures"}\n```\n\n[Public reference](https://example.org/research)';
export function AssistantMarkdownSample() {
  return (
    <section>
      <h3 className="text-headline-medium">
        Safe formatted assistant reply · illustrative fixture
      </h3>
      <AssistantMessageContent
        role="assistant"
        content={ASSISTANT_MARKDOWN_SAMPLE}
      />
      <h3 className="text-headline-medium">User prompts remain literal text</h3>
      <AssistantMessageContent
        role="user"
        content="Please compare **models** and keep `exact` evidence."
      />
    </section>
  );
}
