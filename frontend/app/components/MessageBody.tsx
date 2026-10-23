"use client";

import { tokenizeMessageLinks } from "../messageLinks";
import { isNativeMesilApp } from "../nativeShare";

type MessageBodyProps = {
  text: string;
  as?: "span" | "p";
  className?: string;
  collapsed?: boolean;
  onOpenDetail?: () => void;
};

function linkedText(text: string) {
  const nativeApp = isNativeMesilApp();
  return tokenizeMessageLinks(text).map((part, index) =>
    part.kind === "link" ? (
      <a
        key={`link-${index}`}
        className="message-web-link"
        href={part.href}
        target={nativeApp ? undefined : "_blank"}
        rel="noopener noreferrer"
      >
        {part.text}
      </a>
    ) : (
      <span key={`text-${index}`}>{part.text}</span>
    ),
  );
}

export function MessageBody({
  text,
  as = "span",
  className = "",
  collapsed = false,
  onOpenDetail,
}: MessageBodyProps) {
  if (collapsed) {
    return (
      <div className={`message-body-content ${className} long-message-preview`.trim()}>
        <span className="long-message-preview-body">{linkedText(text)}</span>
        {onOpenDetail ? (
          <button
            type="button"
            className="long-message-open"
            onClick={onOpenDetail}
          >
            전체 보기
          </button>
        ) : null}
      </div>
    );
  }

  if (as === "p") {
    return <p className={`message-body-content ${className}`.trim()}>{linkedText(text)}</p>;
  }
  return <span className={`message-body-content ${className}`.trim()}>{linkedText(text)}</span>;
}
