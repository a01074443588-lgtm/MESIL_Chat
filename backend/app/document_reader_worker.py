"""Short-lived internal parser process. Its pipe is never application log output."""
import contextlib
import io
import json
import sys


def main():
    # Linux candidate contains the parser within a bounded child process.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    except (ImportError, ValueError, OSError):
        pass
    request = json.loads(sys.stdin.read(16_384))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from .document_reading import read_document, read_scanned_page, DocumentReadError
        detail = {}
        try:
            def ocr_page(path):
                from .image_ocr_runtime import attachment_image_runtime
                from .ocr import is_pathological_handwriting_output, NO_TEXT_RESPONSES
                with attachment_image_runtime({"model_call_count":0}) as runtime:
                    detail.update(ocr_provider=runtime.provider,ocr_model=runtime.model)
                    value=read_scanned_page(path)
                if is_pathological_handwriting_output(value):
                    raise DocumentReadError("page_read_failed")
                return "" if value.strip() in NO_TEXT_RESPONSES else value
            result=read_document(request["path"],request["extension"],ocr_page=ocr_page)
            payload={"text":result.text,"locations":result.locations,"ocr_page_count":result.ocr_page_count,"runtime":detail}
        except DocumentReadError as exc:
            payload={"error_type":exc.code}
        except Exception:
            payload={"error_type":"corrupt_document"}
    print(json.dumps(payload,ensure_ascii=False))


if __name__ == "__main__":
    main()
