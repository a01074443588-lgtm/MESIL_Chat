import {useState, useSyncExternalStore, type SetStateAction} from "react";
import type {Message} from "./types";

export type RoomComposerDraft = {
  body: string;
  files: File[];
  replyTarget: Message | null;
  residentIds: string[];
  reportImage: boolean;
  noticeMode: boolean;
  optionsOpen: boolean;
  feedback: string;
  error: string;
  sending: boolean;
  progress: number | null;
  preparing: boolean;
  requestId: string;
};
type Entry = {draft: RoomComposerDraft; generation: number; pending: number; tail: Promise<void>; job?: object};
export type ComposerSend = {roomId: string; draft: RoomComposerDraft; entry: Entry; token: object};

function emptyDraft(): RoomComposerDraft {
  return {body: "", files: [], replyTarget: null, residentIds: [], reportImage: false,
    noticeMode: false, optionsOpen: false, feedback: "", error: "", sending: false,
    progress: null, preparing: false, requestId: crypto.randomUUID()};
}

/** Account-lifetime memory only. Never persists text or File objects to disk. */
export class RoomComposerStore {
  private entries = new Map<string, Entry>();
  private listeners = new Set<() => void>();
  private revision = 0;
  private empty = emptyDraft();
  subscribe = (listener: () => void) => {this.listeners.add(listener); return () => {this.listeners.delete(listener);};};
  getRevision = () => this.revision;
  private changed() {this.revision += 1; this.listeners.forEach(listener => listener());}
  private entry(roomId: string): Entry {
    let entry = this.entries.get(roomId);
    if (!entry) {
      entry = {draft: emptyDraft(), generation: 0, pending: 0, tail: Promise.resolve()};
      this.entries.set(roomId, entry);
    }
    return entry;
  }
  get(roomId: string | null) {return roomId ? this.entry(roomId).draft : this.empty;}
  patch(roomId: string, patch: Partial<RoomComposerDraft>) {
    const entry = this.entry(roomId);
    entry.draft = {...entry.draft, ...patch};
    this.changed();
  }
  setField<K extends keyof RoomComposerDraft>(roomId: string | null, key: K, value: SetStateAction<RoomComposerDraft[K]>) {
    const current = this.get(roomId)[key];
    const next = typeof value === "function"
      ? (value as (previous: RoomComposerDraft[K]) => RoomComposerDraft[K])(current) : value;
    if (roomId) this.patch(roomId, {[key]: next});
    else {this.empty = {...this.empty, [key]: next}; this.changed();}
  }
  setFiles(roomId: string | null, files: File[]) {
    if (!roomId) return;
    const entry = this.entry(roomId);
    entry.generation += 1; // Explicit cancel/removal invalidates pending reads, not navigation.
    this.patch(roomId, {files, error: ""});
  }
  prepareFiles(roomId: string, added: File[], prepare: (current: File[], added: File[]) => Promise<File[]>) {
    const entry = this.entry(roomId);
    if (entry.draft.sending) return Promise.resolve();
    const generation = entry.generation;
    entry.pending += 1;
    this.patch(roomId, {preparing: true, error: ""});
    const current = () => this.entries.get(roomId) === entry && entry.generation === generation;
    entry.tail = entry.tail.then(async () => {
      if (!current() || entry.draft.sending) return;
      const files = await prepare(entry.draft.files, added);
      if (!current() || entry.draft.sending) return;
      this.patch(roomId, {files, error: "", ...(!files.some(file => file.type.startsWith("image/")) ? {reportImage: false} : {})});
    }).catch(reason => {
      if (current()) this.patch(roomId, {error: reason instanceof Error ? reason.message : "파일을 읽지 못했습니다. 다시 선택해 주세요."});
    }).finally(() => {
      entry.pending -= 1;
      if (this.entries.get(roomId) === entry) this.patch(roomId, {preparing: entry.pending > 0});
    });
    return entry.tail;
  }
  beginSend(roomId: string): ComposerSend | null {
    const entry = this.entry(roomId);
    if (entry.draft.sending || entry.pending > 0 || (!entry.draft.body.trim() && !entry.draft.files.length)) return null;
    const token = {};
    const draft = {...entry.draft, files: [...entry.draft.files], residentIds: [...entry.draft.residentIds]};
    entry.job = token;
    this.patch(roomId, {body: "", replyTarget: null, residentIds: [], sending: true, progress: draft.files.length ? 0 : null,
      feedback: "", error: ""});
    return {roomId, entry, token, draft};
  }
  isCurrent(job: ComposerSend) {return this.entries.get(job.roomId) === job.entry && job.entry.job === job.token;}
  progress(job: ComposerSend, progress: number) {if (this.isCurrent(job)) this.patch(job.roomId, {progress});}
  succeed(job: ComposerSend, feedback: string) {
    if (!this.isCurrent(job)) return false;
    job.entry.job = undefined;
    job.entry.generation += 1;
    this.patch(job.roomId, {files: [], reportImage: false, noticeMode: false, optionsOpen: false,
      sending: false, progress: null, error: "", feedback, requestId: crypto.randomUUID()});
    return true;
  }
  fail(job: ComposerSend, error: string) {
    if (!this.isCurrent(job)) return false;
    job.entry.job = undefined;
    this.patch(job.roomId, {...job.draft, sending: false, progress: null, preparing: false, feedback: "", error});
    return true;
  }
  adoptSharedText(roomId: string) {
    if (!this.empty.body) return;
    const draft = this.get(roomId);
    if (draft.sending || draft.body) return; // Never replace an existing room draft.
    this.patch(roomId, {body: this.empty.body, feedback: this.empty.feedback});
    this.empty = emptyDraft();
    this.changed();
  }
  clear() {this.entries.clear(); this.empty = emptyDraft(); this.changed();}
  retainRooms(roomIds: Set<string>) {
    let changed = false;
    for (const id of this.entries.keys()) if (!roomIds.has(id)) {this.entries.delete(id); changed = true;}
    if (changed) this.changed();
  }
  hasPendingWork() {
    return Boolean(this.empty.body.trim()) || [...this.entries.values()].some(({draft}) => draft.body.trim() || draft.files.length || draft.preparing || draft.sending);
  }
  activeUploads() {return [...this.entries.values()].filter(({draft}) => draft.sending && draft.files.length > 0).length;}
}

export function useRoomComposer(roomId: string | null) {
  const [store] = useState(() => new RoomComposerStore());
  useSyncExternalStore(store.subscribe, store.getRevision, () => 0);
  const draft = store.get(roomId);
  const setter = <K extends keyof RoomComposerDraft>(key: K) => (value: SetStateAction<RoomComposerDraft[K]>) => store.setField(roomId, key, value);
  return {store, draft,
    setMessageBody: setter("body"), setReplyTarget: setter("replyTarget"),
    setSelectedResidentIds: setter("residentIds"), setReportImage: setter("reportImage"),
    setNoticeMode: setter("noticeMode"), setComposerOptionsOpen: setter("optionsOpen"),
    setSendFeedback: setter("feedback"), setFiles: (files: File[]) => store.setFiles(roomId, files)};
}
