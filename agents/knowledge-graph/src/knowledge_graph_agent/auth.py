import asyncio
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from platform_runtime.settings import settings

bearer = HTTPBearer(auto_error=False)


class JwtVerifier:
    def __init__(self) -> None:
        self.jwks = PyJWKClient(settings.jwt_jwks_url) if settings.jwt_jwks_url else None

    async def __call__(
        self,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    ) -> dict[str, str]:
        if settings.auth_disabled:
            return {"sub": "local-development"}
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Bearer token required")
        if not self.jwks or not settings.jwt_audience or not settings.jwt_issuer:
            raise HTTPException(status_code=503, detail="JWT validation is not configured")
        try:
            key = await asyncio.to_thread(
                self.jwks.get_signing_key_from_jwt, credentials.credentials
            )
            return jwt.decode(
                credentials.credentials,
                key.key,
                algorithms=["RS256", "ES256"],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


verify_jwt = JwtVerifier()
