from datetime import timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import LoginSession, User, utcnow
from .security import (
    InvalidReviewerSessionToken,
    is_mentor_full_reviewer,
    is_local_development_request,
    token_digest,
    validate_reviewer_session_user,
)


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_current_session_and_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    db: Session = Depends(get_db),
) -> tuple[LoginSession, User]:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    login_session = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == token_digest(session_token))
    )
    now = utcnow()
    if (
        login_session is None
        or login_session.revoked_at is not None
        or _as_utc(login_session.expires_at) <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었습니다.")
    user = db.get(User, login_session.user_id)
    if login_session.impersonated_by_user_id is not None:
        controller = db.get(User, login_session.impersonated_by_user_id)
        if (
            not settings.dev_launcher_active
            or controller is None
            or not controller.is_active
            or controller.username != settings.dev_launcher_username
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="개발 시험 세션이 종료되었습니다.",
            )
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="접근이 차단된 계정입니다.")
    if (
        user.staff is None
        or user.staff.organization_id != user.organization_id
        or user.staff.deleted_at is not None
    ):
        login_session.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="계정 소속정보를 확인할 수 없어 접근이 차단되었습니다.",
        )
    if not user.staff.is_active or user.staff.employment_status != "active":
        login_session.revoked_at = now
        db.commit()
        detail = (
            "휴직 중에는 로그인할 수 없습니다. 복직 처리 후 다시 로그인해 주세요."
            if user.staff.employment_status == "leave"
            else "접근이 차단된 계정입니다."
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    try:
        reviewer_context = validate_reviewer_session_user(
            session_token,
            user,
            now=now,
        )
    except InvalidReviewerSessionToken as exc:
        login_session.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="심사위원 체험 세션이 종료되었습니다.",
        ) from exc
    reviewer_experience = (
        reviewer_context.experience if reviewer_context is not None else None
    )
    login_session._reviewer_experience = reviewer_experience
    user._reviewer_experience = reviewer_experience
    if (
        user.username == settings.dev_launcher_username
        and not settings.dev_launcher_active
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="개발자 런처가 비활성화되었습니다.",
        )
    if (
        (
            user.username == settings.dev_launcher_username
            or login_session.impersonated_by_user_id is not None
        )
        and not is_local_development_request(request)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="요청한 페이지를 찾을 수 없습니다.",
        )
    login_session.last_seen_at = now
    db.commit()
    return login_session, user


def get_current_user(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
) -> User:
    login_session, user = auth
    if user.must_change_password and login_session.impersonated_by_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="계속하려면 먼저 임시 비밀번호를 변경해야 합니다.",
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin" and not is_mentor_full_reviewer(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한이 필요합니다.")
    return user


def admin_conversation_access_is_active(login_session: LoginSession) -> bool:
    expires_at = login_session.admin_conversation_access_expires_at
    return expires_at is not None and _as_utc(expires_at) > utcnow()


def require_admin_conversation_access(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
) -> tuple[LoginSession, User]:
    login_session, user = auth
    if user.must_change_password and login_session.impersonated_by_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="계속하려면 먼저 임시 비밀번호를 변경해야 합니다.",
        )
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다.",
        )
    if getattr(user, "_reviewer_experience", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="심사 체험 화면에서는 업무대화를 열람할 수 없습니다.",
        )
    if not admin_conversation_access_is_active(login_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="업무대화를 보려면 관리자 비밀번호를 다시 입력해 주세요.",
        )
    return login_session, user
