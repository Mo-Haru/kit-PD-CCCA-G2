"""人生会議アプリの永続化層。Python 3.10+。

依存: Flask-SQLAlchemy>=3.1,<3.2 / SQLAlchemy>=2.0,<2.1
共通のdbはapp.extensionsで生成し、create_appで初期化する。
モデルのimportでメタデータへ登録され、flask --app run init-dbで未作成テーブルを
追加する。既存テーブルの列変更はapp/schema_upgrade.pyの手順で行う。
このパッケージは永続化層である。WebAuthn検証、暗号化、認可、APIはblueprintsとservicesに置く。

実装時の契約:
* パスキーの秘密鍵、PRF出力、平文の回答・復号鍵はサーバーへ送らない。
* wrapped_keyは端末で暗号化済みの鍵、nonceは端末で生成した値を保存。
* 暗号方式v1: AES-256-GCM、12-byte nonce、ciphertext末尾に16-byte tag。
  鍵導出v1: HKDF-SHA-256。用途別infoとAADの正規化仕様はクライアントと
  別途合意する。カラムの存在だけでは暗号化は実装されない。
* AuthSession/Challengeの所有者は検証済み認証結果から決める。
* 所有者による参照/変更制限、CSRF、期限/失効確認はAPI側で毎回行う。
* QRの鍵はURL fragmentに入れ、QRは端末内で生成。公開APIで鍵envelopeを返さない。
* 質問の公開済み版、回答文書、共有内容は追記方式。UPDATEはサービス層で禁止。
  SharedSnapshotは公開設定だけ変更可能。revoked_atの取消しもサービス層で禁止。
* 新しいパスキー追加時は既存の鍵を端末で復号して新envelopeを作る。
  パスキー登録だけでは既存データを復号できない。
* PRF対応/出力を実機確認。登録UVと認証UV、challenge、origin、RP ID等を
  WebAuthnライブラリで検証。認証レスポンスからPRF出力を除外して送信。
* 認証器の署名カウンターは同期パスキーで0の場合もある。単純な増分必須にしない。
* 全日時はtimezone-aware UTC。APIではISO 8601のZ表記に変換する。
  NotificationSetting.local_timeは通知先タイムゾーンでの壁時計時刻。
* SQLiteの外部キーをbaseモジュールのconnectイベントで有効化する。
* 外部キー削除は原則RESTRICT（暗黙のNO ACTION）。退会は関連データを明示順序で削除する。
* メタデータ（所有者、時刻、共有状態等）は運営に見える。

公開QR読取APIの例（概念）:
    SELECT shared_snapshots WHERE id=:id AND is_enabled=true
      AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > :now)
    # access_mode='qr'を確認。recipientsの場合は必ず認証+ACLを確認する。
    # ownerのアカウント状態も確認し、ciphertext/nonce/版だけを明示選択する。
    # HTTP Cache-Control: no-store。返却後に相手が保存した内容の取消しはできない。

WebAuthnChallengeはflow_binding_hashで開始ブラウザと結び付ける。
検証成功時のUPDATE ... WHERE consumed_at IS NULL AND expires_at > :nowを
原子的に実行し、更新件数1件の場合のみ認証を成立させる。
"""

from app.extensions import db
from app.models.base import utcnow
from app.models.user import PasskeyCredential, User
from app.models.auth import AuthSession, WebAuthnChallenge
from app.models.document import (
    PrivateAnswerDocument,
    RecoveryKeyEnvelope,
    UserDataKey,
    UserKeyEnvelope,
)
from app.models.sharing import SharedSnapshot, ShareRecipient
from app.models.question import (
    GlossaryTerm,
    Question,
    QuestionCategory,
    QuestionGlossaryTerm,
    QuestionOption,
)
from app.models.audit import AuditLog
from app.models.notification import ExternalAccount, NotificationDelivery, NotificationSetting
from app.models.protection import (
    DeviceMigration,
    FamilyConnection,
    FamilyInvitation,
    FamilyShare,
    FamilyShareOwnerCopy,
    FamilyShareVersion,
    QrShare,
    QrShareVersion,
    QrViewSession,
    RateLimitBucket,
    SchemaMigration,
    SessionReauth,
    UserEncryptionKey,
    UserKeyBundle,
    VerifiedContact,
)

__all__ = [
    "db", "utcnow", "User", "PasskeyCredential", "WebAuthnChallenge",
    "AuthSession", "UserDataKey", "UserKeyEnvelope", "PrivateAnswerDocument",
    "SharedSnapshot", "QuestionCategory", "Question", "QuestionOption",
    "GlossaryTerm", "QuestionGlossaryTerm", "AuditLog", "RecoveryKeyEnvelope",
    "ExternalAccount", "NotificationSetting", "NotificationDelivery", "ShareRecipient",
    "SchemaMigration", "UserEncryptionKey", "UserKeyBundle", "SessionReauth",
    "RateLimitBucket", "VerifiedContact", "FamilyInvitation", "FamilyConnection",
    "FamilyShare", "FamilyShareVersion", "FamilyShareOwnerCopy", "QrShare",
    "QrShareVersion", "QrViewSession", "DeviceMigration",
]
