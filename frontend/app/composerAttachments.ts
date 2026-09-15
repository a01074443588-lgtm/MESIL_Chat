import { attachmentNameError } from './attachmentFormats.ts';

export const MAX_ATTACHMENTS_PER_MESSAGE = 10;
const MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024;
const MAX_ATTACHMENTS_TOTAL_BYTES = 100 * 1024 * 1024;

const selectionKeys = new WeakMap<File, number>();
let nextSelectionKey = 0;
export function attachmentSelectionKey(file: File) {
  if (!selectionKeys.has(file)) selectionKeys.set(file, ++nextSelectionKey);
  return selectionKeys.get(file)!;
}

export function attachmentSelectionError(files: File[]) {
  if (files.length > MAX_ATTACHMENTS_PER_MESSAGE) return `파일은 한 메시지에 최대 10개까지 첨부할 수 있습니다. 현재 ${files.length}개를 선택했습니다.`;
  for (const file of files) {
    if (file.size <= 0) return `빈 파일은 첨부할 수 없습니다: ${file.name}`;
    const error = attachmentNameError(file.name);
    if (error) return error;
    if (file.size > MAX_ATTACHMENT_BYTES) return `파일 하나의 최대 크기는 30MB입니다. 다시 선택해 주세요: ${file.name}`;
  }
  if (files.reduce((sum, file) => sum + file.size, 0) > MAX_ATTACHMENTS_TOTAL_BYTES) return '한 메시지의 파일 전체 용량은 100MB 이하여야 합니다.';
  return '';
}

export function readComposerClipboard(data: Pick<DataTransfer, 'files' | 'items' | 'types'>):
  {kind: 'text'} | {kind: 'unavailable'} | {kind: 'files'; files: File[]} {
  // Browsers often expose the same file through both collections.
  const files = Array.from(data.files);
  if (!files.length) {
    for (const item of Array.from(data.items)) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
  }
  if (files.length) return {kind: 'files', files};
  if (Array.from(data.types).includes('Files') || Array.from(data.items).some(item => item.kind === 'file')) return {kind: 'unavailable'};
  return {kind: 'text'};
}

const fingerprints = new WeakMap<File, Promise<string>>();
async function fingerprint(file: File) {
  let pending = fingerprints.get(file);
  if (!pending) {
    pending = file.arrayBuffer().then(bytes => crypto.subtle.digest('SHA-256', bytes))
      .then(hash => Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join(''));
    fingerprints.set(file, pending);
  }
  return pending;
}

export async function prepareComposerFiles(current: File[], added: File[], options: {clipboard?: boolean} = {}) {
  const incoming = added.map(file => {
    const extension = ({'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'} as Record<string, string>)[file.type];
    return options.clipboard && !file.name && extension
      ? new File([file], `image.${extension}`, {type: file.type, lastModified: file.lastModified}) : file;
  });
  // Validate before reading bytes; never hash an oversized or disallowed file.
  for (const file of incoming) {
    const error = attachmentSelectionError([file]);
    if (error) throw new Error(error);
  }
  const selected = [...current];
  const known = new Set<string>();
  for (const file of current) known.add(await fingerprint(file));
  for (const file of incoming) {
    const hash = await fingerprint(file);
    if (known.has(hash)) continue;
    known.add(hash);
    const capture = options.clipboard && file.type.startsWith('image/') && /^(?:image|blob|clipboard|screenshot)(?:\.[a-z0-9]+)?$/i.test(file.name);
    const name = capture ? `clipboard-${hash.slice(0, 16)}.${file.type === 'image/jpeg' ? 'jpg' : file.type === 'image/webp' ? 'webp' : 'png'}` : file.name;
    const normalized = capture ? new File([file], name, {type: file.type, lastModified: file.lastModified}) : file;
    fingerprints.set(normalized, Promise.resolve(hash));
    selected.push(normalized);
    const error = attachmentSelectionError(selected);
    if (error) throw new Error(error);
  }
  return selected;
}
