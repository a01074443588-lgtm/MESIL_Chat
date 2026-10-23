import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require("react-dom/server");

function compileModule(url, compilerOptions = {}) {
  let source;
  try {
    source = readFileSync(url, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") {
      assert.fail(`구현 파일이 아직 없습니다: ${url.pathname}`);
    }
    throw error;
  }
  return ts.transpileModule(source, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      ...compilerOptions,
    },
  }).outputText;
}

function loadBody({ native = false } = {}) {
  const linksCompiled = compileModule(
    new URL("../app/messageLinks.ts", import.meta.url),
  );
  const linksModule = { exports: {} };
  new Function("require", "module", "exports", linksCompiled)(
    require,
    linksModule,
    linksModule.exports,
  );

  const componentCompiled = compileModule(
    new URL("../app/components/MessageBody.tsx", import.meta.url),
  );
  const componentModule = { exports: {} };
  const deps = {
    "react/jsx-runtime": require("react/jsx-runtime"),
    "../messageLinks": linksModule.exports,
    "../nativeShare": { isNativeMesilApp: () => native },
  };
  new Function("require", "module", "exports", componentCompiled)(
    (name) => deps[name] ?? require(name),
    componentModule,
    componentModule.exports,
  );
  return componentModule.exports.MessageBody;
}

function render(props, options) {
  const MessageBody = loadBody(options);
  return renderToStaticMarkup(MessageBody(props));
}

test("browser message links open in a new tab with opener protection", () => {
  const html = render({
    text: "회의 https://example.com/join?id=7#room",
    className: "bubble-text",
  });
  assert.match(html, /class="message-body-content bubble-text"/);
  assert.match(html, /href="https:\/\/example\.com\/join\?id=7#room"/);
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, />https:\/\/example\.com\/join\?id=7#room<\/a>/);
});

test("native message links use normal navigation for Capacitor ACTION_VIEW", () => {
  const html = render(
    { text: "www.example.org/회의" },
    { native: true },
  );
  assert.match(html, /href="https:\/\/www\.example\.org\/회의"/);
  assert.doesNotMatch(html, /target=/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test("message text stays escaped and unsafe markup never becomes an element", () => {
  const original = '<img src=x onerror=alert(1)> javascript:alert(1)';
  const html = render({ text: original, as: "p" });
  assert.match(html, /^<p/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /<a/);
});

test("collapsed long text keeps the link separate from the full-view button", () => {
  const html = render({
    text: "긴 안내 https://example.com/full",
    collapsed: true,
    onOpenDetail() {},
    className: "bubble-text",
  });
  assert.match(html, /^<div[^>]*class="message-body-content bubble-text long-message-preview"/);
  assert.match(html, /<a[^>]+>https:\/\/example\.com\/full<\/a>/);
  assert.match(html, /<button[^>]+class="long-message-open"[^>]*>전체 보기<\/button>/);
  assert.doesNotMatch(html, /<button[^>]*>[\s\S]*<a/);
});

test("paragraph messages preserve newlines for CSS without rewriting text", () => {
  const html = render({ text: "첫 줄\n둘째 https://example.com", as: "p" });
  assert.match(html, /^<p[^>]*><span>첫 줄\n둘째 <\/span><a/);
});
