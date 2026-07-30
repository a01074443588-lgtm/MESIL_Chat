#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, "..");
const baseUrl = process.env.SMCODI_BASE_URL || "http://127.0.0.1:8080";
const origin = new URL(baseUrl).origin;
const writerUsername = process.env.SMCODI_WRITER_USERNAME;
const processorUsername = process.env.SMCODI_PROCESSOR_USERNAME;
const roomName = process.env.SMCODI_ROOM_NAME || "시설 전체방";
const devUsername = process.env.SMCODI_DEV_USERNAME;
const devPassword = process.env.SMCODI_DEV_PASSWORD;
const imageDirectory = process.env.SMCODI_IMAGE_DIR;
const audioDirectory = process.env.SMCODI_AUDIO_DIR;
const outputDirectory =
  process.env.SMCODI_VALIDATION_OUTPUT ||
  path.join(projectRoot, "output", "media-pipeline-validation");
const statePath =
  process.env.SMCODI_VALIDATION_STATE ||
  path.join(outputDirectory, "media-pipeline-state.json");
const decisionsPath =
  process.env.SMCODI_VALIDATION_DECISIONS ||
  path.join(outputDirectory, "media-pipeline-decisions.json");
const phase = process.argv[2] || "ingest";
const runLabel =
  process.env.SMCODI_VALIDATION_LABEL ||
  `MEDIA-PIPELINE-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`;

if (!devUsername || !devPassword) {
  throw new Error(
    "SMCODI_DEV_USERNAME과 SMCODI_DEV_PASSWORD 환경변수가 필요합니다.",
  );
}
if (!writerUsername || !processorUsername) {
  throw new Error(
    "SMCODI_WRITER_USERNAME과 SMCODI_PROCESSOR_USERNAME 환경변수가 필요합니다.",
  );
}
if (!imageDirectory) {
  throw new Error("SMCODI_IMAGE_DIR 환경변수가 필요합니다.");
}
if (!audioDirectory) {
  throw new Error("SMCODI_AUDIO_DIR 환경변수가 필요합니다.");
}

const imageSamples = [
  {
    key: "image-01",
    fileName: "ChatGPT Image 2026년 7월 28일 오전 10_59_14 (1).png",
    expectedResidents: [
      "시설(가명)003",
      "시설(가명)011",
      "시설(가명)018",
      "시설(가명)027",
      "시설(가명)041",
    ],
    expectedText: [
      "시설(가명)003: 아침 식사 1/2 정도 드심. 식후 “속이 답답하다”고 말씀하셔서 물 자주 드시도록 권유함. 오전에는 침상에서 휴식하심.",
      "시설(가명)011: 오전 프로그램 참여 권유드렸으나 피곤하다고 하시며 거부함. 오후에는 기분이 풀리셔서 복도 산책 10분 정도 하심.",
      "시설(가명)018: 화장실 2회 도움드림. 배변 양상 보통이며 기저귀 교체 시 협조적이셨음.",
      "시설(가명)027: 점심 후 “집에 가야 한다”는 말씀 반복하시며 보호자 찾으심. 말벗 제공 후 안정을 찾으심.",
      "시설(가명)041: 오후 활력 확인 시 어지럼 호소 없었고 간식과 물 섭취 양호하셨음.",
    ].join("\n"),
  },
  {
    key: "image-02",
    fileName: "ChatGPT Image 2026년 7월 28일 오전 10_58_23 (4).png",
    expectedResidents: [
      "시설(가명)005",
      "시설(가명)009",
      "시설(가명)014",
      "시설(가명)032",
      "시설(가명)044",
    ],
    expectedText: [
      "시설(가명)005: 밤잠을 설쳤다고 하시며 오전 내내 졸려하심. 점심 식사 후 침상에서 30분 정도 수면하심.",
      "시설(가명)009: 목욕 후 전신 상태 확인하며 로션 도포함. 피부 발적이나 특이사항은 관찰되지 않았음.",
      "시설(가명)014: 점심 식사 시 반찬 편식하셔서 죽 조금 추가 제공함. 식사는 2/3 정도 드심.",
      "시설(가명)032: 복도를 여러 차례 배회하시며 다른 어르신 침상 쪽으로 가셔서 주의 말씀드림. 이후 직원과 함께 거실에서 쉬심.",
      "시설(가명)044: 보호자 면회 후 기분이 좋아 보이셨고 저녁 식사는 완식하심.",
    ].join("\n"),
  },
  {
    key: "image-03",
    fileName: "ChatGPT Image 2026년 7월 28일 오전 10_58_23 (3).png",
    expectedResidents: [
      "시설(가명)006",
      "시설(가명)013",
      "시설(가명)021",
      "시설(가명)029",
      "시설(가명)038",
    ],
    expectedText: [
      "시설(가명)006: 아침 세면 도와드리려 하니 처음에는 싫다고 하셨으나 10분 뒤 다시 말씀드리니 협조해주심.",
      "시설(가명)013: 점심 식사 중 기침을 2~3회 하셔서 천천히 드시도록 안내드림. 이후 안정적으로 식사 이어가심.",
      "시설(가명)021: 손등을 자꾸 긁으셔서 확인해보니 붉은 자국 약간 있어 연고 도포함.",
      "시설(가명)029: 오후에 “엄마 찾는다”고 하시며 불안해하심. 음악 들려드리고 말벗해드리니 점차 안정되심.",
      "시설(가명)038: 화장실 이동 중 비틀거리는 모습 보여 휠체어로 안전하게 이동 도와드림.",
    ].join("\n"),
  },
  {
    key: "image-04",
    fileName: "ChatGPT Image 2026년 7월 28일 오전 10_58_22 (2).png",
    expectedResidents: [
      "시설(가명)008",
      "시설(가명)019",
      "시설(가명)025",
      "시설(가명)033",
      "시설(가명)049",
    ],
    expectedText: [
      "시설(가명)008: 오전 독서 프로그램에 참여하셨고 대화 시 반응 또렷하셨음. 기분도 안정적이셨음.",
      "시설(가명)019: 오후 간식은 거부하셨고 물만 조금 드심. 저녁 식사는 평소보다 적게 드셨음.",
      "시설(가명)025: 낮 동안 계속 누워계시며 허리 통증 있다고 말씀하심. 체위 변경 도와드리고 쿠션 받쳐드림.",
      "시설(가명)033: 저녁 식사 전 체온 37.6 확인되어 간호팀에 전달 후 상태 관찰 중임.",
      "시설(가명)049: 보호자와 통화 후 눈물 보이셨으나 말벗 제공 후 안정 찾으셨음.",
    ].join("\n"),
  },
  {
    key: "image-05",
    fileName: "ChatGPT Image 2026년 7월 28일 오전 10_58_24 (5).png",
    expectedResidents: [
      "시설(가명)001",
      "시설(가명)016",
      "시설(가명)022",
      "시설(가명)035",
      "시설(가명)047",
    ],
    expectedText: [
      "시설(가명)001: 오전 혈당 체크하였고 식전 128, 식후 176으로 확인됨. 어지럼이나 구토 증상은 없으셨음.",
      "시설(가명)016: 약 복용 시 입안에 머금고 계셔서 물 더 드리고 삼키시는 것 확인함.",
      "시설(가명)022: 오후 물리치료 다녀오신 후 피곤하다고 하심. 보행 시 1인 부축 필요하였음.",
      "시설(가명)035: 오후 배변 1회 하셨으며 다소 묽은 양상 보여 간호팀에 전달함.",
      "시설(가명)047: 새벽에 일찍 깨셨다고 하시며 낮잠을 길게 주무셔서 오후 프로그램은 쉬심.",
    ].join("\n"),
  },
].map((sample) => ({
  ...sample,
  kind: "image",
  filePath: path.join(imageDirectory, sample.fileName),
  mimeType: "image/png",
}));

const audioSamples = [
  {
    key: "audio-01",
    fileName: "003어르신졸음.wav",
    expectedAnchors: ["시설(가명)003", "졸음"],
  },
  {
    key: "audio-02",
    fileName: "005어르신손발톱정리.wav",
    expectedAnchors: ["시설(가명)005", "손발톱"],
  },
  {
    key: "audio-03",
    fileName: "007면회포도.wav",
    expectedAnchors: ["시설(가명)007", "면회", "포도"],
  },
  {
    key: "audio-04",
    fileName: "보호자상담사례.wav",
    expectedAnchors: ["보호자", "상담"],
  },
  {
    key: "audio-05",
    fileName: "복약확인사례.wav",
    expectedAnchors: ["약", "복용", "확인"],
  },
  {
    key: "audio-06",
    fileName: "수치시간사례.wav",
    expectedAnchors: ["체온", "시간"],
  },
  {
    key: "audio-07",
    fileName: "여러어르신포함사례.wav",
    expectedAnchors: ["시설(가명)"],
  },
  {
    key: "audio-08",
    fileName: "인지배회사례.wav",
    expectedAnchors: ["배회"],
  },
  {
    key: "audio-09",
    fileName: "피부발적사례.wav",
    expectedAnchors: ["피부", "발적"],
  },
  {
    key: "audio-10",
    fileName: "낙상직전사례.wav",
    expectedAnchors: ["낙상"],
  },
].map((sample) => ({
  ...sample,
  kind: "audio",
  filePath: path.join(audioDirectory, sample.fileName),
  mimeType: "audio/wav",
}));

class ApiClient {
  constructor(name) {
    this.name = name;
    this.cookies = new Map();
  }

  storeCookies(headers) {
    let values = [];
    if (typeof headers.getSetCookie === "function") {
      values = headers.getSetCookie();
    } else {
      const combined = headers.get("set-cookie");
      if (combined) {
        values = combined.split(/,(?=\s*[^;,]+=)/);
      }
    }
    for (const value of values) {
      const pair = value.split(";", 1)[0];
      const separator = pair.indexOf("=");
      if (separator < 1) continue;
      const name = pair.slice(0, separator).trim();
      const cookieValue = pair.slice(separator + 1).trim();
      if (cookieValue) this.cookies.set(name, cookieValue);
      else this.cookies.delete(name);
    }
  }

  cookieHeader() {
    return [...this.cookies.entries()]
      .map(([name, value]) => `${name}=${value}`)
      .join("; ");
  }

  async request(route, options = {}) {
    const headers = new Headers(options.headers || {});
    const cookie = this.cookieHeader();
    if (cookie) headers.set("cookie", cookie);
    if (!["GET", "HEAD"].includes(options.method || "GET")) {
      headers.set("origin", origin);
    }
    const response = await fetch(`${baseUrl}${route}`, {
      ...options,
      headers,
    });
    this.storeCookies(response.headers);
    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = raw;
      }
    }
    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? payload.detail
          : raw;
      throw new Error(
        `${this.name} ${options.method || "GET"} ${route} 실패 ` +
          `(${response.status}): ${detail}`,
      );
    }
    return payload;
  }

  get(route) {
    return this.request(route);
  }

  postJson(route, body = {}) {
    return this.request(route, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  patchJson(route, body) {
    return this.request(route, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }
}

async function loginAs(client, targetUsername) {
  await client.postJson("/api/auth/login", {
    username: devUsername,
    password: devPassword,
  });
  const users = await client.get("/api/dev/users");
  const target = users.find((user) => user.username === targetUsername);
  if (!target) {
    throw new Error(`런처에서 ${targetUsername} 사용자를 찾을 수 없습니다.`);
  }
  await client.postJson(`/api/dev/switch/${target.id}`, {});
  const current = await client.get("/api/auth/me");
  if (current.username !== targetUsername) {
    throw new Error(
      `${targetUsername} 전환 검증 실패: 현재 사용자는 ${current.username}`,
    );
  }
  return current;
}

async function uploadSample(client, roomId, sample, index, total) {
  const binary = await readFile(sample.filePath);
  const form = new FormData();
  form.append(
    "body",
    `[${runLabel}] ${sample.kind === "image" ? "가명 보고서 이미지" : "가명 음성보고"} ` +
      `${index}/${total} · ${sample.key}`,
  );
  form.append("message_type", "chat");
  form.append("report_image", sample.kind === "image" ? "true" : "false");
  form.append(
    "files",
    new Blob([binary], { type: sample.mimeType }),
    sample.fileName,
  );
  const started = performance.now();
  const message = await client.request(
    `/api/rooms/${roomId}/messages-with-files`,
    { method: "POST", body: form },
  );
  return {
    message,
    uploadElapsedMs: Math.round(performance.now() - started),
    uploadObservedAt: new Date().toISOString(),
  };
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function getWorkItemByMessage(processor, messageId) {
  const items = await processor.get("/api/work-items");
  return (
    items.find((item) => item.source_snapshot.message_id === messageId) || null
  );
}

async function waitForExtraction(processor, messageId, timeoutMs) {
  const started = performance.now();
  let lastStatus = null;
  while (performance.now() - started < timeoutMs) {
    const item = await getWorkItemByMessage(processor, messageId);
    const attachment = item?.message?.attachments?.[0] || null;
    const extraction = attachment?.text_extraction || null;
    if (extraction?.status !== lastStatus) {
      lastStatus = extraction?.status || "work_item_pending";
      console.log(`  ${messageId}: ${lastStatus}`);
    }
    if (extraction?.status === "completed" || extraction?.status === "reviewed") {
      return {
        item,
        attachment,
        extraction,
        elapsedMs: Math.round(performance.now() - started),
      };
    }
    if (extraction?.status === "failed") {
      return {
        item,
        attachment,
        extraction,
        elapsedMs: Math.round(performance.now() - started),
      };
    }
    await sleep(1500);
  }
  throw new Error(`${messageId} 판독이 ${timeoutMs / 1000}초 안에 끝나지 않았습니다.`);
}

function normalizeForMetric(text) {
  return String(text || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[^\p{L}\p{N}]/gu, "");
}

function levenshtein(left, right) {
  const a = [...left];
  const b = [...right];
  const previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let row = 1; row <= a.length; row += 1) {
    const current = [row];
    for (let column = 1; column <= b.length; column += 1) {
      current[column] = Math.min(
        current[column - 1] + 1,
        previous[column] + 1,
        previous[column - 1] + (a[row - 1] === b[column - 1] ? 0 : 1),
      );
    }
    previous.splice(0, previous.length, ...current);
  }
  return previous[b.length];
}

function textAccuracy(actual, expected) {
  const normalizedActual = normalizeForMetric(actual);
  const normalizedExpected = normalizeForMetric(expected);
  if (!normalizedExpected.length) return null;
  const distance = levenshtein(normalizedActual, normalizedExpected);
  return {
    normalizedActualLength: normalizedActual.length,
    normalizedExpectedLength: normalizedExpected.length,
    editDistance: distance,
    characterErrorRate: Number((distance / normalizedExpected.length).toFixed(4)),
    characterAccuracy: Number(
      Math.max(0, 1 - distance / normalizedExpected.length).toFixed(4),
    ),
  };
}

function includesNormalized(text, value) {
  return normalizeForMetric(text).includes(normalizeForMetric(value));
}

function linkedResidentNames(item) {
  return (item?.message?.resident_links || []).map(
    (link) => link.resident.display_name,
  );
}

async function ingest() {
  await mkdir(outputDirectory, { recursive: true });
  const allSamples = [...imageSamples, ...audioSamples];
  for (const sample of allSamples) {
    await readFile(sample.filePath);
  }

  const writer = new ApiClient("writer");
  const processor = new ApiClient("processor");
  const writerUser = await loginAs(writer, writerUsername);
  const processorUser = await loginAs(processor, processorUsername);
  const rooms = await writer.get("/api/rooms");
  const room = rooms.find((candidate) => candidate.name === roomName);
  if (!room) {
    throw new Error(`${writerUsername} 계정에서 ${roomName}을 찾을 수 없습니다.`);
  }

  const results = [];
  console.log(
    `전송 ${writerUser.full_name} → ${room.name}, 검토 ${processorUser.full_name}`,
  );
  for (let index = 0; index < allSamples.length; index += 1) {
    const sample = allSamples[index];
    console.log(
      `[${index + 1}/${allSamples.length}] ${sample.fileName} 전송 및 판독`,
    );
    const upload = await uploadSample(
      writer,
      room.id,
      sample,
      index + 1,
      allSamples.length,
    );
    const extraction = await waitForExtraction(
      processor,
      upload.message.id,
      sample.kind === "image" ? 6 * 60_000 : 12 * 60_000,
    );
    const rawText = extraction.extraction?.extracted_text || "";
    const detectedNames = linkedResidentNames(extraction.item);
    const result = {
      key: sample.key,
      kind: sample.kind,
      fileName: sample.fileName,
      messageId: upload.message.id,
      workItemId: extraction.item?.id || null,
      attachmentId: extraction.attachment?.id || null,
      uploadElapsedMs: upload.uploadElapsedMs,
      extractionObservedElapsedMs: extraction.elapsedMs,
      extractionStatus: extraction.extraction?.status || "missing",
      provider: extraction.extraction?.provider || null,
      modelName: extraction.extraction?.model_name || null,
      completedAt: extraction.extraction?.completed_at || null,
      rawText,
      detectedResidentNames: detectedNames,
      expectedResidents: sample.expectedResidents || [],
      expectedAnchors: sample.expectedAnchors || [],
      expectedText: sample.expectedText || null,
      textMetric:
        sample.kind === "image"
          ? textAccuracy(rawText, sample.expectedText)
          : null,
      residentRecall:
        sample.kind === "image"
          ? {
              detected: sample.expectedResidents.filter((name) =>
                detectedNames.includes(name),
              ).length,
              expected: sample.expectedResidents.length,
            }
          : null,
      anchorChecks:
        sample.kind === "audio"
          ? sample.expectedAnchors.map((anchor) => ({
              anchor,
              found: includesNormalized(rawText, anchor),
            }))
          : [],
      errorMessage: extraction.extraction?.error_message || null,
    };
    results.push(result);
    await writeFile(
      statePath,
      JSON.stringify(
        {
          version: 1,
          phase: "ingested",
          runLabel,
          baseUrl,
          room: { id: room.id, name: room.name },
          writer: { username: writerUsername, name: writerUser.full_name },
          processor: {
            username: processorUsername,
            name: processorUser.full_name,
          },
          startedAt: results[0]?.uploadObservedAt || null,
          updatedAt: new Date().toISOString(),
          results,
        },
        null,
        2,
      ),
      "utf8",
    );
  }
  console.log(`판독 상태 저장: ${statePath}`);
}

async function reviewResidentLinks(
  processor,
  item,
  expectedResidentNames,
) {
  const expected = new Set(expectedResidentNames);
  const decisions = [];
  for (const link of item.message.resident_links || []) {
    if (link.source === "manual" || link.status !== "candidate") continue;
    const targetStatus = expected.has(link.resident.display_name)
      ? "confirmed"
      : "rejected";
    await processor.patchJson(
      `/api/messages/${item.message.id}/resident-links/${link.resident.id}`,
      { status: targetStatus },
    );
    decisions.push({
      residentName: link.resident.display_name,
      status: targetStatus,
    });
  }
  return decisions;
}

async function finalize() {
  const state = JSON.parse(await readFile(statePath, "utf8"));
  const decisions = JSON.parse(await readFile(decisionsPath, "utf8"));
  const byKey = new Map(
    (decisions.results || []).map((decision) => [decision.key, decision]),
  );
  const processor = new ApiClient("processor");
  await loginAs(processor, processorUsername);
  const finalResults = [];

  for (let index = 0; index < state.results.length; index += 1) {
    const recorded = state.results[index];
    const decision = byKey.get(recorded.key);
    if (!decision) {
      throw new Error(`${recorded.key} 검토 결정이 없습니다.`);
    }
    console.log(
      `[${index + 1}/${state.results.length}] ${recorded.fileName} 담당자 검토 및 AI 초안`,
    );
    let item = await getWorkItemByMessage(processor, recorded.messageId);
    if (!item) throw new Error(`${recorded.messageId} 업무항목을 찾을 수 없습니다.`);
    const attachment = item.message.attachments[0];
    const reviewStarted = performance.now();
    const reviewedText = decision.reviewedText || recorded.rawText;
    await processor.patchJson(
      `/api/attachments/${attachment.id}/text-extraction`,
      {
        decision: "direct_edit",
        reviewed_text: reviewedText,
      },
    );
    item = await getWorkItemByMessage(processor, recorded.messageId);
    const residentDecisions = await reviewResidentLinks(
      processor,
      item,
      decision.residentNames || [],
    );
    item = await getWorkItemByMessage(processor, recorded.messageId);
    let aiResult = null;
    let aiError = null;
    let aiElapsedMs = null;
    if (item.resident && !(item.message.resident_links || []).some(
      (link) => link.status === "candidate",
    )) {
      const aiStarted = performance.now();
      try {
        aiResult = await processor.postJson(
          `/api/work-items/${item.id}/ai-review`,
          {},
        );
      } catch (error) {
        aiError = String(error.message || error);
      }
      aiElapsedMs = Math.round(performance.now() - aiStarted);
    } else {
      aiError = item.resident
        ? "확인되지 않은 어르신 후보가 남아 있습니다."
        : "확정된 어르신이 없어 AI 초안을 만들지 않았습니다.";
    }
    const current = aiResult || (await getWorkItemByMessage(processor, recorded.messageId));
    finalResults.push({
      ...recorded,
      reviewedText,
      reviewerNote: decision.note || null,
      expectedResidentNames: decision.residentNames || [],
      residentDecisions,
      confirmedResidentNames: current.source_snapshot.resident_names || [],
      reviewElapsedMs: Math.round(performance.now() - reviewStarted),
      aiElapsedMs,
      aiError,
      aiState: current.ai_state,
      aiGenerator: current.ai_generator,
      aiSuggestion: current.ai_suggestion,
      documentDrafts: current.document_drafts,
    });
  }

  const finalState = {
    ...state,
    phase: "finalized",
    finalizedAt: new Date().toISOString(),
    results: finalResults,
  };
  const finalJsonPath = path.join(outputDirectory, "media-pipeline-final.json");
  await writeFile(finalJsonPath, JSON.stringify(finalState, null, 2), "utf8");
  await writeFile(
    path.join(outputDirectory, "media-pipeline-report.md"),
    buildMarkdownReport(finalState),
    "utf8",
  );
  console.log(`최종 JSON: ${finalJsonPath}`);
  console.log(
    `최종 보고서: ${path.join(outputDirectory, "media-pipeline-report.md")}`,
  );
}

function average(values) {
  const numbers = values.filter((value) => Number.isFinite(value));
  if (!numbers.length) return null;
  return Math.round(numbers.reduce((sum, value) => sum + value, 0) / numbers.length);
}

function formatSeconds(milliseconds) {
  return Number.isFinite(milliseconds)
    ? `${(milliseconds / 1000).toFixed(1)}초`
    : "-";
}

function escapeCell(value) {
  return String(value ?? "-")
    .replace(/\|/g, "\\|")
    .replace(/\r?\n/g, "<br>");
}

function buildMarkdownReport(state) {
  const images = state.results.filter((result) => result.kind === "image");
  const audios = state.results.filter((result) => result.kind === "audio");
  const imageAccuracy = average(
    images.map((result) =>
      result.textMetric
        ? Math.round(result.textMetric.characterAccuracy * 10_000)
        : NaN,
    ),
  );
  const residentExpected = images.reduce(
    (sum, result) => sum + (result.residentRecall?.expected || 0),
    0,
  );
  const residentDetected = images.reduce(
    (sum, result) => sum + (result.residentRecall?.detected || 0),
    0,
  );
  const anchorTotal = audios.reduce(
    (sum, result) => sum + result.anchorChecks.length,
    0,
  );
  const anchorFound = audios.reduce(
    (sum, result) =>
      sum + result.anchorChecks.filter((check) => check.found).length,
    0,
  );
  const aiSuccess = state.results.filter(
    (result) => result.aiSuggestion && result.documentDrafts?.length,
  ).length;
  const lines = [
    "# 가명 이미지·음성 실제 채팅 흐름 통합 검증",
    "",
    `- 실행 표식: \`${state.runLabel}\``,
    `- 채팅방: ${state.room.name}`,
    `- 전송: ${state.writer.name} (${state.writer.username})`,
    `- 검토: ${state.processor.name} (${state.processor.username})`,
    `- 완료: ${state.finalizedAt}`,
    "",
    "## 요약",
    "",
    `- 이미지 OCR 정규화 문자 정확도 평균: ${
      imageAccuracy === null ? "-" : `${(imageAccuracy / 100).toFixed(2)}%`
    }`,
    `- 이미지 가명 어르신명 탐지: ${residentDetected}/${residentExpected}`,
    `- 음성 핵심어 자동 확인: ${anchorFound}/${anchorTotal}`,
    `- AI 정리와 일일서류 초안 생성: ${aiSuccess}/${state.results.length}`,
    `- 판독 관찰시간 평균: ${formatSeconds(
      average(state.results.map((result) => result.extractionObservedElapsedMs)),
    )}`,
    `- AI 처리시간 평균: ${formatSeconds(
      average(state.results.map((result) => result.aiElapsedMs)),
    )}`,
    "",
    "## 건별 결과",
    "",
    "|자료|종류|판독|판독시간|정확도/핵심어|어르신|AI|서류 초안|",
    "|---|---|---|---:|---|---|---|---:|",
  ];
  for (const result of state.results) {
    const accuracy =
      result.kind === "image"
        ? result.textMetric
          ? `${(result.textMetric.characterAccuracy * 100).toFixed(1)}%`
          : "-"
        : `${result.anchorChecks.filter((check) => check.found).length}/${result.anchorChecks.length}`;
    lines.push(
      `|${escapeCell(result.fileName)}|${result.kind === "image" ? "이미지" : "음성"}|` +
        `${escapeCell(result.extractionStatus)}|${formatSeconds(result.extractionObservedElapsedMs)}|` +
        `${accuracy}|${escapeCell((result.confirmedResidentNames || []).join(", "))}|` +
        `${escapeCell(result.aiGenerator || result.aiError)}|${result.documentDrafts?.length || 0}|`,
    );
  }
  lines.push("", "## 판독 원문과 AI 결과", "");
  for (const result of state.results) {
    lines.push(
      `### ${result.key} · ${result.fileName}`,
      "",
      `- 메시지: \`${result.messageId}\``,
      `- 업무항목: \`${result.workItemId}\``,
      `- 판독기: ${result.provider || "-"} / ${result.modelName || "-"}`,
      `- 확인 어르신: ${(result.confirmedResidentNames || []).join(", ") || "없음"}`,
      `- AI: ${result.aiGenerator || result.aiError || "-"}`,
      `- 서류: ${(result.documentDrafts || [])
        .map((draft) => draft.document_type)
        .join(", ") || "없음"}`,
      ...(result.reviewerNote
        ? [`- 검토 메모: ${result.reviewerNote}`]
        : []),
      "",
      "판독 원문:",
      "",
      "```text",
      result.rawText || "(없음)",
      "```",
      "",
      "담당자 확인문:",
      "",
      "```text",
      result.reviewedText || "(없음)",
      "```",
      "",
      "AI 요약:",
      "",
      result.aiSuggestion?.summary || result.aiError || "(없음)",
      "",
    );
    for (const draft of result.documentDrafts || []) {
      lines.push(
        `#### ${draft.document_type}`,
        "",
        draft.content,
        "",
        ...(draft.verification_questions?.length
          ? [
              "확인할 내용:",
              "",
              ...draft.verification_questions.map((question) => `- ${question}`),
              "",
            ]
          : []),
      );
    }
  }
  lines.push(
    "## 해석상 주의",
    "",
    "- 이미지 정확도는 제공된 정답문과 공백·문장부호를 제외한 문자 편집거리로 계산했습니다.",
    "- 음성은 별도 정답 대본이 없어 전체 단어 정확도가 아니라 파일별 핵심어 검출로 확인했습니다.",
    "- 이름·시간·숫자·약·신체 부위·위험 내용은 자동 확정하지 않았고, 검토 결정 파일에 명시한 경우만 확정했습니다.",
    "- 생성된 문서는 확정 기록이 아니라 담당자가 확인할 초안입니다.",
    "",
  );
  return lines.join("\n");
}

if (phase === "ingest") {
  await ingest();
} else if (phase === "finalize") {
  await finalize();
} else {
  throw new Error("사용법: node scripts/validate_media_pipeline.mjs ingest|finalize");
}
