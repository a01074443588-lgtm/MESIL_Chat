export type MessageTextPart =
  | { kind: "text"; text: string }
  | { kind: "link"; text: string; href: string };

// Recognize common bare web domains too; never infer arbitrary file suffixes.
const webDomain = "(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+(?:com|net|org|kr|edu|gov|io|ai|info|biz|me|co|app|dev|news|site|online|tv)";
const explicitWebUrlPattern = new RegExp(
  `(?:https?:\\/\\/|www\\.)[^\\s<>"'\x60]+|${webDomain}(?![\\p{L}\\p{N}._-])(?::[0-9]{1,5})?(?:[/?#][^\\s<>"'\x60]*)?`,
  "giu",
);
const unsafePrefixCharacter = /[\p{L}\p{N}@._-]/u;
const trailingPunctuation = new Set([".", ",", "!", "?", ";", ":", "。", "，", "、", "…"]);
const bracketPairs = [
  ["(", ")"],
  ["[", "]"],
  ["{", "}"],
] as const;

function countCharacter(value: string, character: string) {
  return [...value].filter((item) => item === character).length;
}

function splitTrailingPunctuation(candidate: string) {
  let linkText = candidate;
  let suffix = "";

  while (linkText && trailingPunctuation.has(linkText.at(-1) ?? "")) {
    suffix = `${linkText.at(-1)}${suffix}`;
    linkText = linkText.slice(0, -1);
  }

  for (const [opening, closing] of bracketPairs) {
    while (
      linkText.endsWith(closing) &&
      countCharacter(linkText, closing) > countCharacter(linkText, opening)
    ) {
      suffix = `${closing}${suffix}`;
      linkText = linkText.slice(0, -1);
    }
  }

  return { linkText, suffix };
}

function safeHref(text: string) {
  const href = /^https?:\/\//iu.test(text) ? text : `https://${text}`;
  try {
    const parsed = new URL(href);
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname
        || parsed.username || parsed.password) return null;
    return href;
  } catch {
    return null;
  }
}

function appendText(parts: MessageTextPart[], text: string) {
  if (!text) return;
  const previous = parts.at(-1);
  if (previous?.kind === "text") {
    previous.text += text;
  } else {
    parts.push({ kind: "text", text });
  }
}

export function tokenizeMessageLinks(text: string): MessageTextPart[] {
  if (!text) return [];

  const parts: MessageTextPart[] = [];
  let cursor = 0;
  explicitWebUrlPattern.lastIndex = 0;

  for (const match of text.matchAll(explicitWebUrlPattern)) {
    const start = match.index ?? 0;
    const candidate = match[0];
    const previousCharacter = start > 0 ? text[start - 1] : "";

    const explicitScheme = /^https?:\/\//iu.test(candidate);
    const blockedPrefix = explicitScheme ? /[a-z0-9@._-]/iu : unsafePrefixCharacter;
    if (previousCharacter && blockedPrefix.test(previousCharacter)) {
      continue;
    }
    // Do not turn a substring of an email/unsupported URI into a web link.
    if (/(?:[a-z][a-z0-9+.-]*:\/\/|(?:javascript|data|file|intent|mailto|vbscript):)[^\s]*$/iu.test(text.slice(0, start))) {
      continue;
    }

    const { linkText, suffix } = splitTrailingPunctuation(candidate);
    const href = safeHref(linkText);
    if (!href) continue;

    appendText(parts, text.slice(cursor, start));
    parts.push({ kind: "link", text: linkText, href });
    appendText(parts, suffix);
    cursor = start + candidate.length;
  }

  appendText(parts, text.slice(cursor));
  return parts;
}
