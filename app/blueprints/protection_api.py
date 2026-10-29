"""パスキー、回答の暗号文、家族共有、QR閲覧、端末移行のAPI。"""

from flask import Blueprint

from app.services import answer_service, auth_service, family_service, qr_service, transfer_service
from app.services.support import finish, json_body

bp = Blueprint("protection_api", __name__)


def _ok(payload, status=200):
    return payload, status


@bp.post("/api/v1/auth/register/options")
def register_options():
    return finish(auth_service.begin_registration(json_body()))


@bp.post("/api/v1/auth/register/verify")
def register_verify():
    return finish(auth_service.finish_registration(json_body()))


@bp.post("/api/v1/auth/login/options")
def login_options():
    return finish(auth_service.begin_login(json_body()))


@bp.post("/api/v1/auth/login/verify")
def login_verify():
    return finish(auth_service.finish_login(json_body()))


@bp.post("/api/v1/auth/logout")
def logout():
    return finish(auth_service.logout())


@bp.get("/api/v1/auth/session")
def session_state():
    return _ok(auth_service.session_state())


@bp.post("/api/v1/auth/unlock/options")
def unlock_options():
    return finish(auth_service.begin_unlock())


@bp.post("/api/v1/auth/unlock/verify")
def unlock_verify():
    return finish(auth_service.finish_unlock(json_body()))


@bp.get("/api/v1/auth/passkeys")
def passkeys():
    return _ok(auth_service.list_passkeys())


@bp.post("/api/v1/auth/passkeys/options")
def add_passkey_options():
    return finish(auth_service.begin_add_passkey())


@bp.post("/api/v1/auth/passkeys/verify")
def add_passkey_verify():
    return finish(auth_service.finish_add_passkey(json_body()))


@bp.post("/api/v1/auth/passkeys/<passkey_id>/disable")
def disable_passkey(passkey_id):
    return finish(auth_service.disable_passkey(passkey_id, json_body()))


@bp.post("/api/v1/auth/passkeys/<passkey_id>/proof/options")
def passkey_proof_options(passkey_id):
    return finish(auth_service.begin_passkey_proof(passkey_id))


@bp.post("/api/v1/auth/passkeys/<passkey_id>/proof/verify")
def passkey_proof_verify(passkey_id):
    return _ok(auth_service.finish_passkey_proof(passkey_id, json_body()))


@bp.get("/api/v1/auth/envelopes")
def envelopes():
    return _ok(auth_service.own_envelopes())


@bp.post("/api/v1/encryption-keys")
def encryption_key():
    return finish(auth_service.register_encryption_key(json_body()))


@bp.get("/api/v1/encryption-keys/<user_id>")
def encryption_keys(user_id):
    return _ok(auth_service.list_encryption_keys(user_id))


@bp.get("/api/v1/answers/contract")
def answer_contract():
    return _ok(answer_service.contract())


@bp.get("/api/v1/answers/questions")
def answer_questions():
    return _ok(answer_service.question_index())


@bp.get("/api/v1/answers/current")
def answer_current():
    return _ok(answer_service.current_answer())


@bp.put("/api/v1/answers/current")
def answer_save():
    return _ok(answer_service.save_answer(json_body()))


@bp.post("/api/v1/family/invitations")
def invitation_create():
    return _ok(family_service.create_invitation(json_body()))


@bp.get("/api/v1/family/invitations")
def invitation_list():
    return _ok(family_service.list_invitations())


@bp.post("/api/v1/family/invitations/request")
def invitation_request():
    return _ok(family_service.request_open_invitation(json_body()))


@bp.post("/api/v1/family/invitations/confirm-link")
def invitation_confirm_link():
    return _ok(family_service.confirm_invitation_link(json_body()))


@bp.post("/api/v1/family/invitations/<invitation_id>/confirm")
def invitation_confirm(invitation_id):
    return _ok(family_service.confirm_invitation(invitation_id, json_body()))


@bp.post("/api/v1/family/invitations/<invitation_id>/confirmation")
def invitation_reissue(invitation_id):
    return _ok(family_service.reissue_confirmation(invitation_id))


@bp.post("/api/v1/family/invitations/<invitation_id>/reject")
def invitation_reject(invitation_id):
    return _ok(family_service.reject_invitation(invitation_id))


@bp.get("/api/v1/family/connections")
def connections():
    return _ok(family_service.list_connections())


@bp.post("/api/v1/family/connections/<connection_id>/disconnect")
def disconnect(connection_id):
    return _ok(family_service.disconnect(connection_id))


@bp.post("/api/v1/family/shares")
def share_create():
    return _ok(family_service.create_share(json_body()))


@bp.get("/api/v1/family/shares")
def share_list():
    return _ok(family_service.list_own_shares())


@bp.post("/api/v1/family/shares/<share_id>/update")
def share_update(share_id):
    return _ok(family_service.update_share(share_id, json_body()))


@bp.post("/api/v1/family/shares/<share_id>/stop")
def share_stop(share_id):
    return _ok(family_service.stop_share(share_id))


@bp.post("/api/v1/family/shares/<share_id>/expiry")
def share_expiry(share_id):
    return _ok(family_service.change_expiry(share_id, json_body()))


@bp.get("/api/v1/family/shares/<share_id>/history")
def share_history(share_id):
    return _ok(family_service.own_history(share_id))


@bp.get("/api/v1/family/received")
def received_list():
    return _ok(family_service.list_received())


@bp.get("/api/v1/family/received/<share_id>")
def received_one(share_id):
    return _ok(family_service.get_received(share_id))


@bp.post("/api/v1/qr-shares")
def qr_create():
    return _ok(qr_service.create_share(json_body()))


@bp.get("/api/v1/qr-shares")
def qr_list():
    return _ok(qr_service.list_shares())


@bp.get("/api/v1/qr-shares/<share_id>")
def qr_get(share_id):
    return _ok(qr_service.get_share(share_id))


@bp.post("/api/v1/qr-shares/<share_id>/update")
def qr_update(share_id):
    return _ok(qr_service.update_share(share_id, json_body()))


@bp.post("/api/v1/qr-shares/<share_id>/stop")
def qr_stop(share_id):
    return _ok(qr_service.stop_share(share_id))


@bp.post("/api/v1/qr-shares/<share_id>/expiry")
def qr_expiry(share_id):
    return _ok(qr_service.change_expiry(share_id, json_body()))


@bp.post("/api/v1/qr-shares/<share_id>/start")
def qr_start(share_id):
    return finish(qr_service.start_view(share_id, json_body()))


@bp.get("/api/v1/qr-view/current")
def qr_current():
    return _ok(qr_service.current_view())


@bp.post("/api/v1/qr-view/end")
def qr_end():
    return finish(qr_service.end_view())


@bp.post("/api/v1/transfer/start")
def transfer_start():
    return _ok(transfer_service.start_migration())


@bp.post("/api/v1/transfer/bind")
def transfer_bind():
    return _ok(transfer_service.bind(json_body()))


@bp.get("/api/v1/transfer/target/status")
def transfer_target_status():
    return _ok(transfer_service.target_status())


@bp.get("/api/v1/transfer/target/bundle")
def transfer_target_bundle():
    return _ok(transfer_service.download_bundle())


@bp.post("/api/v1/transfer/passkey/options")
def transfer_passkey_options():
    return finish(transfer_service.begin_target_passkey())


@bp.post("/api/v1/transfer/passkey/verify")
def transfer_passkey_verify():
    return _ok(transfer_service.finish_target_passkey(json_body()))


@bp.get("/api/v1/transfer/<migration_id>")
def transfer_status(migration_id):
    return _ok(transfer_service.source_status(migration_id))


@bp.post("/api/v1/transfer/<migration_id>/approve")
def transfer_approve(migration_id):
    return _ok(transfer_service.approve(migration_id, json_body()))


@bp.post("/api/v1/transfer/<migration_id>/bundle")
def transfer_bundle(migration_id):
    return _ok(transfer_service.upload_bundle(migration_id, json_body()))


@bp.post("/api/v1/transfer/<migration_id>/decrypt-confirmed")
def transfer_decrypt_confirmed(migration_id):
    return _ok(transfer_service.acknowledge_decrypt(migration_id, json_body()))


@bp.post("/api/v1/transfer/<migration_id>/finalize")
def transfer_finalize(migration_id):
    return _ok(transfer_service.finalize(migration_id, json_body()))


@bp.post("/api/v1/transfer/<migration_id>/cancel")
def transfer_cancel(migration_id):
    return _ok(transfer_service.cancel(migration_id))
