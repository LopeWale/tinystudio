/** Illustrative profile computed with core.quality.profile from the three previewRecords, never server evidence. */
export const previewQuality = {
  count: 3,
  buckets: {
    uncategorized: 0,
    training: 2,
    validation: 1,
  },
  labels: [
    ["support", 2],
    ["billing", 1],
  ],
  sources: [["illustrative", 3]],
  turns: {
    mean: 2.0,
    max: 2,
    with_system_prompt: 0,
    distinct_system_prompts: 0,
  },
  user_chars: {
    summary: {
      min: 42,
      median: 64,
      mean: 59.3,
      p95: 72,
      max: 72,
    },
    histogram: {
      bins: [42, 45, 48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 78],
      counts: [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0],
    },
  },
  answer_chars: {
    summary: {
      min: 7,
      median: 18,
      mean: 28.0,
      p95: 59,
      max: 59,
    },
    histogram: {
      bins: [7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57, 62, 67],
      counts: [1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
    },
  },
  total_chars: {
    summary: {
      min: 49,
      median: 82,
      mean: 87.3,
      p95: 131,
      max: 131,
    },
    histogram: {
      bins: [49, 56, 63, 70, 77, 84, 91, 98, 105, 112, 119, 126, 133],
      counts: [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1],
    },
  },
  approx_tokens: {
    total: 65,
    per_row_mean: 21.8,
  },
  duplicates: {
    exact_user_groups: 0,
    exact_user_rows: 0,
    exact_conversation_groups: 0,
    exact_conversation_rows: 0,
    near_pairs: [],
    near_pair_count: 0,
    examples: [],
  },
  outliers: {
    too_short: [],
    too_long: [],
    empty_like: [],
    fences: {
      low: 20,
      high: 254,
    },
  },
  format: {
    answers_json: 1,
    answers_json_pct: 33.3,
  },
  ratings: {
    count: 3,
    histogram: {
      bins: [9, 10],
      counts: [3],
    },
    mean: 9.0,
  },
  pii: {
    emails: 0,
    phones: 0,
    urls: 0,
    ids: 0,
    cards: 0,
    redactable_rows: 0,
  },
  generated: "2026-10-28T00:00:00Z",
};
