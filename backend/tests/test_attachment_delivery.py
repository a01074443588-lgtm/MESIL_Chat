from types import SimpleNamespace
from uuid import uuid4

from app import main
from fastapi.responses import FileResponse


def test_download_releases_database_before_streaming_file(monkeypatch, tmp_path):
    organization_id = uuid4()
    stored = tmp_path / "sample.jpg"
    stored.write_bytes(b"sample-image")
    attachment = SimpleNamespace(
        message_id=uuid4(),
        organization_id=organization_id,
        storage_key=stored.name,
        mime_type="image/jpeg",
    )

    class FakeDb:
        closed = False

        def get(self, model, _identifier):
            if model is main.MessageAttachment:
                return attachment
            if model is main.Message:
                return SimpleNamespace(
                    organization_id=organization_id,
                    lifecycle_status="active",
                )
            return None

        def close(self):
            self.closed = True

    db = FakeDb()
    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(main, "attachment_access_capabilities", lambda *_args: {
        "can_download_original": True,
    })

    response = main.download_attachment(
        uuid4(),
        user=SimpleNamespace(id=uuid4(), organization_id=organization_id),
        db=db,
    )

    assert isinstance(response, FileResponse)
    assert db.closed is True
    assert response.headers["cache-control"] == "private, max-age=86400"
