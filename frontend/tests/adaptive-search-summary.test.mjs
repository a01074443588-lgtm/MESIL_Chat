import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const componentPath = new URL("../app/components/RoomSearchOverlay.tsx", import.meta.url);
const typesPath = new URL("../app/types.ts", import.meta.url);

test("search summary uses one adaptive answer area and only final evidence", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /검색 결과 AI 요약/);
  assert.match(source, /전체 흐름 요약/);
  assert.match(source, /상세 경과 요약/);
  assert.match(source, /AI 검색 요약/);
  assert.match(source, /summary\.generation_verified && summary\.generator/);
  assert.match(source, /기록 기반 요약/);
  assert.match(source, /summary\.summary_sentences\.map/);
  assert.match(source, /summary\.summary_evidence/);
  assert.match(source, /근거 기록 \{summary\.summary_evidence\.length\}건 보기/);
  assert.doesNotMatch(source, /summary\.timeline\.map/);
  assert.doesNotMatch(source, /summary\.resident_summaries\.map/);
  assert.doesNotMatch(source, /<pre>\{summary\.summary\}<\/pre>/);
});

test("summary request preserves every server-validated search filter", async () => {
  const source = await readFile(componentPath, "utf8");

  for (const key of ["query", "message_type", "action_status"]) {
    assert.match(source, new RegExp(`${key}:`));
  }
  assert.match(source, /summaryFlightRef/);
  assert.match(source, /AbortController/);
  assert.match(source, /검색 기록을 정리하고 있습니다\./);
  assert.match(source, /중요한 변화와 경과를 찾고 있습니다\./);
  assert.match(source, /요약과 근거를 확인하고 있습니다\./);
});

test("frontend response type includes verified sentence and timing fields", async () => {
  const source = await readFile(typesPath, "utf8");

  assert.match(source, /generation_verified: boolean/);
  assert.match(source, /summary_sentences:/);
  assert.match(source, /summary_evidence:/);
  assert.match(source, /performance:/);
});
