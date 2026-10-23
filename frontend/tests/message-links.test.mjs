import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);

function loadTokenizer() {
  let source;
  try {
    source = readFileSync(
      new URL("../app/messageLinks.ts", import.meta.url),
      "utf8",
    );
  } catch (error) {
    if (error?.code === "ENOENT") {
      assert.fail("메시지 URL 토큰화 모듈이 아직 구현되지 않았습니다.");
    }
    throw error;
  }
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const mod = { exports: {} };
  new Function("require", "module", "exports", compiled)(require, mod, mod.exports);
  return mod.exports.tokenizeMessageLinks;
}

function parts(text) {
  return loadTokenizer()(text);
}

function reconstruct(items) {
  return items.map((item) => item.text).join("");
}

test("keeps query and fragment in an explicit HTTPS link", () => {
  assert.deepEqual(parts("주소 https://example.com/meeting?id=7#join 확인"), [
    { kind: "text", text: "주소 " },
    {
      kind: "link",
      text: "https://example.com/meeting?id=7#join",
      href: "https://example.com/meeting?id=7#join",
    },
    { kind: "text", text: " 확인" },
  ]);
});

test("recognizes multiple links, newlines, Korean paths and ampersands", () => {
  const original =
    "첫째 https://example.com/회의?name=홍길동&day=1\n둘째 www.example.org/안내#준비";
  const result = parts(original);
  assert.equal(reconstruct(result), original);
  assert.deepEqual(
    result.filter((item) => item.kind === "link"),
    [
      {
        kind: "link",
        text: "https://example.com/회의?name=홍길동&day=1",
        href: "https://example.com/회의?name=홍길동&day=1",
      },
      {
        kind: "link",
        text: "www.example.org/안내#준비",
        href: "https://www.example.org/안내#준비",
      },
    ],
  );
});

test("separates sentence punctuation and unmatched closing brackets", () => {
  const original = "회의(https://example.com/a(b)?x=1#t), 다음입니다.";
  const result = parts(original);
  assert.equal(reconstruct(result), original);
  assert.deepEqual(result, [
    { kind: "text", text: "회의(" },
    {
      kind: "link",
      text: "https://example.com/a(b)?x=1#t",
      href: "https://example.com/a(b)?x=1#t",
    },
    { kind: "text", text: "), 다음입니다." },
  ]);
});

test("does not infer unsafe schemes, HTML or invalid embedded domains as links", () => {
  const original =
    "javascript:alert(1) data:text/html,hi file:///tmp/a intent://bad <img src=x onerror=alert(1)> abcwww.example.invalid";
  const result = parts(original);
  assert.equal(reconstruct(result), original);
  assert.equal(result.some((item) => item.kind === "link"), false);
});

test("preserves invalid-looking explicit URLs as ordinary text", () => {
  for (const original of ["https://", "http://?x=1", "www."]) {
    const result = parts(original);
    assert.equal(reconstruct(result), original);
    assert.equal(result.some((item) => item.kind === "link"), false);
  }
});

test("does not make credential-bearing or deceptive userinfo URLs clickable", () => {
  for (const original of ["https://synthetic:password@example.org/a", "https://example.org@invalid.example/a"]) {
    const result = parts(original);
    assert.equal(reconstruct(result), original);
    assert.equal(result.some((item) => item.kind === "link"), false);
  }
});

test("never changes the stored display text while tokenizing", () => {
  const samples = [
    "",
    "일반 대화",
    "앞https://example.com 뒤",
    "https://example.com/path.",
    "[www.example.com/a_(b)]",
    "https://example.org/j/123?pwd=SYNTHETIC_ONLY",
  ];
  for (const original of samples) {
    assert.equal(reconstruct(parts(original)), original);
  }
});

test("recognizes a web domain without a scheme and keeps its path", () => {
  const original = "자료 example.or.kr/안내?id=7#본문 확인";
  assert.deepEqual(parts(original), [
    { kind: "text", text: "자료 " },
    { kind: "link", text: "example.or.kr/안내?id=7#본문", href: "https://example.or.kr/안내?id=7#본문" },
    { kind: "text", text: " 확인" },
  ]);
});

test("an explicit HTTP scheme may follow Korean text without a space", () => {
  const original = "안내https://example.org/notice";
  const result = parts(original);
  assert.equal(reconstruct(result), original);
  assert.equal(result.filter((p) => p.kind === "link").length, 1);
});

test("bare domains never link inside email, unsupported schemes or filenames", () => {
  for (const original of ["a@example.com", "ftp://example.com/a", "file:///example.com/a", "javascript:example.com/a", "report.pdf", "version1.2", "example.com.exe", "prefixexample.comSuffix"]) {
    assert.equal(parts(original).some((p) => p.kind === "link"), false, original);
    assert.equal(reconstruct(parts(original)), original);
  }
});
