import {
  Capacitor,
  registerPlugin,
  type PluginListenerHandle,
} from "@capacitor/core";

type NativeSharedFile = {
  name: string;
  type: string;
  size: number;
  path: string;
};

type NativeShareResult = {
  files: NativeSharedFile[];
};

type NativeShareReceiverPlugin = {
  consumePendingShare(): Promise<NativeShareResult>;
  releaseSharedFiles(options: { paths: string[] }): Promise<void>;
  addListener(
    eventName: "shareReceived",
    listener: (event: { count: number }) => void,
  ): Promise<PluginListenerHandle>;
};

const nativeShareReceiver = registerPlugin<NativeShareReceiverPlugin>(
  "MesilShareReceiver",
);

export function isNativeMesilApp() {
  return Capacitor.isNativePlatform();
}

export async function consumeNativeSharedFiles(): Promise<File[]> {
  if (!isNativeMesilApp()) return [];
  const result = await nativeShareReceiver.consumePendingShare();
  const entries = Array.isArray(result.files) ? result.files.slice(0, 10) : [];
  const releasePaths = entries.map((entry) => entry.path);
  try {
    const files: File[] = [];
    for (const entry of entries) {
      if (
        !entry.name ||
        !entry.type ||
        !entry.path ||
        !Number.isFinite(entry.size) ||
        entry.size <= 0
      ) {
        continue;
      }
      const localUrl = Capacitor.convertFileSrc(entry.path);
      const response = await fetch(localUrl, { cache: "no-store" });
      if (!response.ok) throw new Error("native-share-fetch-failed");
      const blob = await response.blob();
      if (blob.size !== entry.size) throw new Error("native-share-size-mismatch");
      files.push(new File([blob], entry.name, { type: entry.type }));
    }
    return files;
  } finally {
    await nativeShareReceiver.releaseSharedFiles({ paths: releasePaths }).catch(() => {
      // The copied files live only in private app cache and Android may clear them later.
    });
  }
}

export async function listenForNativeShares(
  listener: () => void,
): Promise<PluginListenerHandle | null> {
  if (!isNativeMesilApp()) return null;
  return nativeShareReceiver.addListener("shareReceived", () => listener());
}
