"""Bound anonymous API work without accounts, cookies, or frontend secrets."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
import asyncio

from app.config import settings
from app.models.database import get_db
from app.services.request_limits import (
    RequestLimitExceeded, client_key_for_request, consume_request_limit,
)


def enforce_limits(request: Request, db=Depends(get_db)):
    path = request.url.path
    if request.method == 'POST' and path.rstrip('/') == '/api/analysis':
        scope, limit, window = 'create', settings.analysis_create_limit, settings.analysis_create_window_seconds
    elif '/export' in path or path.startswith('/api/export/'):
        scope, limit, window = 'export', settings.export_request_limit, settings.export_window_seconds
    else:
        scope, limit, window = 'read', 120, 60
    key = client_key_for_request(request, settings.anonymous_rate_limit_secret)
    request.state.anonymous_client_key = key
    try:
        # Shared global bounds also protect against a flood from many addresses.
        consume_request_limit(db, 'global', scope, limit * 20, window)
        consume_request_limit(db, key, scope, limit, window)
        db.commit()
    except RequestLimitExceeded as exc:
        db.commit()
        raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)}) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(503, 'Service temporarily unavailable') from exc


class RequestBodyLimit:
    """Buffer at most 16 KiB before JSON parsing, including chunked requests."""
    def __init__(self, app, max_bytes=16384):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        body = bytearray()
        async def read_body():
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return False
                body.extend(message.get('body', b''))
                if len(body) > self.max_bytes or not message.get('more_body', False):
                    return True
        try:
            connected = await asyncio.wait_for(read_body(), timeout=10)
        except asyncio.TimeoutError:
            return await JSONResponse({'detail': 'Request body timeout'}, status_code=408)(scope, receive, send)
        if not connected:
            return
        if len(body) > self.max_bytes:
            return await JSONResponse({'detail': 'Request body too large'}, status_code=413)(scope, receive, send)
        consumed = False

        async def limited_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, limited_receive, send)
