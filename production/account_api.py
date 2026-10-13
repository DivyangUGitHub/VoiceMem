"""Endpoints a signed-in user uses on their own account: view, consent, password, export, delete.

Mounted at /account behind SecurityMiddleware, which has already authenticated the caller and
set the tenant. Key holders (``k...`` tenants) have no account record and are refused.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from production.accounts import AccountError
from production.backup import export_zip
from voicemem.utils.common import tenant
from voicemem.utils.common.space import root as memory_root


class _Password(BaseModel):
    old: str
    new: str


class _Delete(BaseModel):
    password: str


class _Consent(BaseModel):
    agree: bool


def build_account_app(accounts, purge, cookie_name: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def me() -> dict:
        uid = tenant.current.get()
        user = accounts.get(uid) if uid and uid.startswith("u") else None
        if not user:
            raise HTTPException(403, "account_required")
        return user

    @app.get("/")
    def show() -> dict:
        return me()

    @app.post("/consent")
    def consent(body: _Consent) -> dict:
        user = me()
        (accounts.give_consent if body.agree else accounts.withdraw_consent)(user["id"])
        return {"consented": body.agree}

    @app.post("/password")
    def password(body: _Password):
        user = me()
        try:
            ok = accounts.change_password(user["id"], body.old, body.new)
        except AccountError as exc:
            raise HTTPException(400, str(exc)) from None
        if not ok:
            raise HTTPException(401, "wrong_password")
        # The version bump signed every session out, including this one.
        return JSONResponse({"ok": True, "signed_out": True},
                            headers={"Set-Cookie": f"{cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})

    @app.get("/export")
    def export():
        user = me()
        spaces = [d for d in memory_root().glob(user["id"] + "_*") if d.is_dir()]
        fd, name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)                      # mkstemp leaves it open; Windows then cannot delete the file
        tmp = Path(name)
        export_zip(spaces, tmp, user)
        return FileResponse(tmp, media_type="application/zip", filename="voicemem-export.zip",
                            background=BackgroundTask(tmp.unlink, missing_ok=True))

    @app.post("/delete")
    def delete(body: _Delete):
        user = me()
        if not accounts.authenticate(user["email"], body.password):
            raise HTTPException(401, "wrong_password")
        removed = purge(user["id"])
        accounts.delete(user["id"])
        return JSONResponse({"deleted": True, "spaces_removed": removed},
                            headers={"Set-Cookie": f"{cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})

    return app
