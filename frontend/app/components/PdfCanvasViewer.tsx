"use client";

import { useEffect, useRef, useState } from "react";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

type ViewerStatus = "loading" | "ready" | "error";

function isCancelledRender(error: unknown) {
  return error instanceof Error && error.name === "RenderingCancelledException";
}

export function PdfCanvasViewer({ url, title }: { url: string; title: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const loadingTaskRef = useRef<PDFDocumentLoadingTask | null>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const [status, setStatus] = useState<ViewerStatus>("loading");
  const [errorMessage, setErrorMessage] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [stageWidth, setStageWidth] = useState(0);
  const [rendering, setRendering] = useState(false);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const updateWidth = () => setStageWidth(Math.floor(stage.clientWidth));
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;

    void (async () => {
      const response = await fetch(url, {
        credentials: "include",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`PDF request failed: ${response.status}`);
      }
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (!contentType.includes("application/pdf")) {
        throw new Error("Attachment response is not a PDF");
      }

      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      const loadingTask = pdfjs.getDocument({
        data: new Uint8Array(await response.arrayBuffer()),
      });
      loadingTaskRef.current = loadingTask;
      const pdfDocument = await loadingTask.promise;
      if (disposed) {
        await loadingTask.destroy();
        return;
      }

      documentRef.current = pdfDocument;
      setPageCount(pdfDocument.numPages);
      setStatus("ready");
    })().catch((error: unknown) => {
      if (disposed || controller.signal.aborted) return;
      console.error("PDF preview failed", error);
      setErrorMessage(
        "PDF를 표시하지 못했습니다. 위의 ‘다른 앱으로 열기’를 이용해 주세요.",
      );
      setStatus("error");
    });

    return () => {
      disposed = true;
      controller.abort();
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      documentRef.current = null;
      const loadingTask = loadingTaskRef.current;
      loadingTaskRef.current = null;
      if (loadingTask) void loadingTask.destroy();
    };
  }, [url]);

  useEffect(() => {
    const pdfDocument = documentRef.current;
    const canvas = canvasRef.current;
    if (status !== "ready" || !pdfDocument || !canvas || stageWidth < 1) return;

    let disposed = false;
    setRendering(true);
    setErrorMessage("");

    void (async () => {
      const page = await pdfDocument.getPage(pageNumber);
      if (disposed) return;

      const baseViewport = page.getViewport({ scale: 1 });
      const fitWidth = Math.max(240, stageWidth - 24);
      const viewport = page.getViewport({
        scale: (fitWidth / baseViewport.width) * zoom,
      });
      const outputScale = Math.min(window.devicePixelRatio || 1, 2);

      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;

      const renderTask = page.render({
        canvas,
        viewport,
        transform:
          outputScale === 1
            ? undefined
            : [outputScale, 0, 0, outputScale, 0, 0],
      });
      renderTaskRef.current = renderTask;
      await renderTask.promise;
      if (!disposed) setRendering(false);
      page.cleanup();
    })().catch((error: unknown) => {
      if (disposed || isCancelledRender(error)) return;
      console.error("PDF page render failed", error);
      setErrorMessage(
        "이 페이지를 표시하지 못했습니다. 위의 ‘다른 앱으로 열기’를 이용해 주세요.",
      );
      setRendering(false);
    });

    return () => {
      disposed = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
    };
  }, [pageNumber, stageWidth, status, zoom]);

  const canMovePrevious = status === "ready" && pageNumber > 1;
  const canMoveNext = status === "ready" && pageNumber < pageCount;

  return (
    <div className="pdf-canvas-viewer">
      <div
        ref={stageRef}
        className="pdf-canvas-stage"
        aria-busy={status === "loading" || rendering}
      >
        {status === "loading" ? (
          <p className="pdf-viewer-status">PDF를 불러오는 중입니다.</p>
        ) : null}
        {errorMessage ? (
          <p className="pdf-viewer-status error" role="alert">
            {errorMessage}
          </p>
        ) : null}
        <canvas
          ref={canvasRef}
          className={rendering ? "is-rendering" : ""}
          aria-label={`${title} ${pageNumber}쪽`}
          hidden={status !== "ready"}
        />
      </div>

      <nav className="pdf-canvas-controls" aria-label="PDF 페이지 이동과 크기 조절">
        <button
          type="button"
          disabled={!canMovePrevious}
          onClick={() => setPageNumber((current) => Math.max(1, current - 1))}
        >
          이전
        </button>
        <span className="pdf-page-count" aria-live="polite">
          {pageCount > 0 ? `${pageNumber} / ${pageCount}` : "—"}
        </span>
        <button
          type="button"
          disabled={!canMoveNext}
          onClick={() =>
            setPageNumber((current) => Math.min(pageCount, current + 1))
          }
        >
          다음
        </button>
        <span className="pdf-control-divider" aria-hidden="true" />
        <button
          type="button"
          aria-label="PDF 작게 보기"
          disabled={status !== "ready" || zoom <= 0.75}
          onClick={() => setZoom((current) => Math.max(0.75, current - 0.25))}
        >
          −
        </button>
        <span className="pdf-zoom-value">{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          aria-label="PDF 크게 보기"
          disabled={status !== "ready" || zoom >= 2}
          onClick={() => setZoom((current) => Math.min(2, current + 0.25))}
        >
          +
        </button>
      </nav>
    </div>
  );
}
