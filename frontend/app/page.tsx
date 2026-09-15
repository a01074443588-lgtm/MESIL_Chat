"use client";

import { useCallback, useState } from "react";
import { ChatApp } from "./components/ChatApp";
import { ChatEntryChoice } from "./components/ChatEntryChoice";

export default function Home() {
  const [entryConfirmed, setEntryConfirmed] = useState(false);
  const continueWeb = useCallback(() => setEntryConfirmed(true), []);

  if (!entryConfirmed) {
    return <ChatEntryChoice onContinueWeb={continueWeb} />;
  }

  return <ChatApp />;
}
