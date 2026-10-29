"""1アカウントが持てる有効な共有の数。大量のアカウントでDBを埋められないよう、作成時に確認する。"""

from __future__ import annotations

from flask import current_app
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import FamilyShare, QrShare, ShareQuotaLock, utcnow
from app.services.support import ensure_rate_bucket, fail, take_rate


def active_share_count(user_id: str, now=None) -> int:
    """QRと家族共有の合計。停止・期限切れ・使用済みの緊急用は数えない。"""
    now = now or utcnow()
    qr = db.session.query(QrShare).filter(
        QrShare.owner_user_id == user_id,
        QrShare.stopped_at.is_(None),
        or_(
            and_(QrShare.mode == "emergency", QrShare.consumed_at.is_(None)),
            # 期限のないリンク（mainに残る無期限モード）も有効として数える。
            and_(QrShare.mode != "emergency", or_(QrShare.expires_at.is_(None), QrShare.expires_at > now)),
        ),
    ).count()
    family = db.session.query(FamilyShare).filter(
        FamilyShare.owner_user_id == user_id,
        FamilyShare.status.in_(("active", "key_pending")),
        or_(FamilyShare.expires_at.is_(None), FamilyShare.expires_at > now),
    ).count()
    return qr + family


def _lock_quota(user_id: str) -> None:
    """同じアカウントの枠の確認から作成までを直列にする。ロックは呼び出し側のコミットかロールバックで外れる。

    ロック行のUPDATEを新しいトランザクションの最初の書き込みにする（PostgreSQLは行ロック、SQLiteは書き込みロック）。
    待っていた要求は、先の要求がコミットした共有を数えてから判断する。
    """
    db.session.commit()
    if db.session.get(ShareQuotaLock, user_id) is None:
        try:
            db.session.add(ShareQuotaLock(user_id=user_id, version=0))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    db.session.execute(
        update(ShareQuotaLock).where(ShareQuotaLock.user_id == user_id)
        .values(version=ShareQuotaLock.version + 1)
    )


def require_capacity(user_id: str) -> None:
    """有効な共有を1件増やせるか確かめる。作成回数には数えない。ロックを取った後に呼ぶ。"""
    limit = int(current_app.config["SHARE_ACTIVE_LIMIT"])
    if active_share_count(user_id) >= limit:
        fail("share_limit", 409,
             f"有効な共有は1アカウント{limit}件までです。不要な共有を停止してから操作してください。",
             actor_id=user_id)


def require_capacity_to_revive(user_id: str, old_expiry, new_expiry, now=None) -> None:
    """期限切れで枠から外れた共有を、期限の変更で有効に戻すときだけ空きを確かめる。new_expiry=Noneは期限なし。"""
    now = now or utcnow()
    was_expired = old_expiry is not None and old_expiry <= now
    becomes_active = new_expiry is None or new_expiry > now
    if was_expired and becomes_active:
        _lock_quota(user_id)
        require_capacity(user_id)


def admit_new_share(user_id: str) -> None:
    """新しい共有を作る前に呼ぶ。呼び出し側は、共有の追加まで途中でコミットせずに1回でコミットする。

    枠の確認・作成回数の加算・共有の追加が同じトランザクションに入るので、
    上限で断った作成や、後の検証で失敗した作成は数えない。
    """
    ident = f"user:{user_id}"
    ensure_rate_bucket("share_create", ident)
    _lock_quota(user_id)
    require_capacity(user_id)
    take_rate("share_create", ident, message="共有の作成が多すぎます。時間をおいてやり直してください。")
