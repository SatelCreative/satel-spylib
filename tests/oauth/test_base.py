from importlib import util
from sys import modules
from typing import List
from unittest.mock import AsyncMock
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse

import pytest
from box import Box  # type: ignore
from httpx import Response
from pydantic.dataclasses import dataclass

from spylib import hmac
from spylib.exceptions import FastAPIImportError
from spylib.oauth.tokens import OAuthJWT
from spylib.utils import JWTBaseModel, domain_to_storename, now_epoch

SHOPIFY_API_KEY = 'API_KEY'
SHOPIFY_SECRET_KEY = 'SECRET_KEY'

TEST_STORE = 'test.myshopify.com'
TEST_DATA = Box(
    dict(
        app_scopes=['write_products', 'read_customers'],
        user_scopes=['write_orders', 'read_products'],
        public_domain='test.testing.com',
        private_key='TESTPRIVATEKEY',
        post_install=AsyncMock(return_value=JWTBaseModel()),
        post_login=AsyncMock(return_value=None),
        api_key=SHOPIFY_API_KEY,
        api_secret_key=SHOPIFY_SECRET_KEY,
    )
)

OFFLINETOKEN_DATA = dict(access_token='OFFLINETOKEN', scope=','.join(TEST_DATA.app_scopes))
ONLINETOKEN_DATA = dict(
    access_token='ONLINETOKEN',
    scope=','.join(TEST_DATA.app_scopes),
    expires_in=86399,
    associated_user_scope=','.join(TEST_DATA.user_scopes),
    associated_user={
        'id': 902541635,
        'first_name': 'John',
        'last_name': 'Smith',
        'email': 'john@example.com',
        'email_verified': True,
        'account_owner': True,
        'locale': 'en',
        'collaborator': False,
    },
)


@dataclass
class MockHTTPResponse:
    status_code: int
    jsondata: dict
    headers: dict = None  # type: ignore

    def json(self):
        return self.jsondata


@pytest.mark.asyncio
async def test_oauth_without_fastapi():
    if 'fastapi' not in modules and util.find_spec('fastapi') is None:
        with pytest.raises(FastAPIImportError):
            import spylib.oauth.fastapi  # noqa: F401


@pytest.mark.asyncio
async def test_oauth_with_fastapi(mocker):
    if 'fastapi' not in modules and util.find_spec('fastapi') is None:
        pytest.skip('fastapi not installed')

    from fastapi import FastAPI  # type: ignore[import]
    from fastapi.testclient import TestClient  # type: ignore[import]

    from spylib.oauth import OfflineTokenModel, OnlineTokenModel
    from spylib.oauth.fastapi import init_oauth_router

    app = FastAPI()

    oauth_router = init_oauth_router(**TEST_DATA)

    app.include_router(oauth_router)
    client = TestClient(app)

    # --------- Test the initialization endpoint -----------

    # Missing shop argument
    response = client.get('/shopify/auth')
    assert response.status_code == 422
    response_json = response.json()
    # the url will change according to the pydantic version installed
    del response_json['detail'][0]['url']
    assert response_json == {
        'detail': [
            {'input': None, 'loc': ['query', 'shop'], 'msg': 'Field required', 'type': 'missing'}
        ],
    }

    # Happy path
    response = client.get('/shopify/auth', params=dict(shop=TEST_STORE), follow_redirects=False)
    query = check_oauth_redirect_url(
        response=response,
        client=client,
        path='/admin/oauth/authorize',
        scope=TEST_DATA.app_scopes,
    )
    state = check_oauth_redirect_query(query=query, scope=TEST_DATA.app_scopes)

    # Callback calls to get tokens
    shopify_request_mock = mocker.patch('httpx.AsyncClient.request', new_callable=AsyncMock)
    shopify_request_mock.side_effect = [
        MockHTTPResponse(status_code=200, jsondata=OFFLINETOKEN_DATA),
        MockHTTPResponse(status_code=200, jsondata=ONLINETOKEN_DATA),
    ]
    # --------- Test the callback endpoint for installation -----------
    query_str = build_callback_query_str(
        params=dict(shop=TEST_STORE, state=state, timestamp=now_epoch(), code='INSTALLCODE'),
        hmac_secret=SHOPIFY_SECRET_KEY,
    )

    response = client.get('/callback', params=query_str, follow_redirects=False)
    query = check_oauth_redirect_url(
        response=response,
        client=client,
        path='/admin/oauth/authorize',
        scope=TEST_DATA.user_scopes,
    )
    state = check_oauth_redirect_query(
        query=query,
        scope=TEST_DATA.user_scopes,
        query_extra={'grant_options[]': ['per-user']},
    )

    assert await shopify_request_mock.called_with(
        method='post',
        url=f'https://{TEST_STORE}/admin/oauth/access_token',
        json={
            'client_id': SHOPIFY_API_KEY,
            'client_secret': SHOPIFY_SECRET_KEY,
            'code': 'INSTALLCODE',
        },
    )

    TEST_DATA.post_install.assert_called_once()
    TEST_DATA.post_install.assert_called_with('test', OfflineTokenModel(**OFFLINETOKEN_DATA))

    # --------- Test the callback endpoint for login -----------
    query_str = build_callback_query_str(
        params=dict(shop=TEST_STORE, state=state, timestamp=now_epoch(), code='LOGINCODE'),
        hmac_secret=SHOPIFY_SECRET_KEY,
        safe='=,&/[]:',
    )

    response = client.get('/callback', params=query_str, follow_redirects=False)
    state = check_oauth_redirect_url(
        response=response,
        client=client,
        path=f'/admin/apps/{SHOPIFY_API_KEY}',
        scope=TEST_DATA.user_scopes,
    )

    assert await shopify_request_mock.called_with(
        method='post',
        url=f'https://{TEST_STORE}/admin/oauth/access_token',
        json={
            'client_id': SHOPIFY_API_KEY,
            'client_secret': SHOPIFY_SECRET_KEY,
            'code': 'LOGINCODE',
        },
    )

    TEST_DATA.post_login.assert_called_once()
    TEST_DATA.post_login.assert_called_with('test', OnlineTokenModel(**ONLINETOKEN_DATA))


def check_oauth_redirect_url(response: Response, client, path: str, scope: List[str]) -> str:
    print(response.text)
    assert response.status_code == 307
    if not response.next_request:
        return ''

    parsed_url = urlparse(str(response.next_request.url))

    expected_parsed_url = ParseResult(
        scheme='https',
        netloc=TEST_STORE,
        path=path,
        query=parsed_url.query,  # We check that separately
        params='',
        fragment='',
    )
    assert parsed_url == expected_parsed_url

    return parsed_url.query


def check_oauth_redirect_query(query: str, scope: List[str], query_extra: dict = {}) -> str:
    parsed_query = parse_qs(query)
    state = parsed_query.pop('state', [''])[0]

    expected_query = dict(
        client_id=[SHOPIFY_API_KEY],
        redirect_uri=[f'https://{TEST_DATA.public_domain}/callback'],
        scope=[','.join(scope)],
    )
    expected_query.update(query_extra)

    assert parsed_query == expected_query

    return state


def build_callback_query_str(params: dict, hmac_secret: str, safe: str = '') -> str:
    query_str = urlencode(params, safe=safe)
    hmac_arg = hmac.calculate_from_message(secret=hmac_secret, message=query_str)
    query_str += '&hmac=' + hmac_arg
    return query_str


callback_params = [
    pytest.param(dict(code='INSTALLCODE')),
    pytest.param(dict(code='INSTALLCODE', host='edfsg4sdf6g4sdg6re')),
    pytest.param(dict(code='INSTALLCODE', random1=12345, random2='giezeogkzor')),
    pytest.param(dict(code='LOGINCODE')),
    pytest.param(dict(code='LOGINCODE', host='edfsg4sdf6g4sdg6re')),
    pytest.param(dict(code='LOGINCODE', random1=12345, random2='giezeogkzor')),
]


@pytest.mark.parametrize('extra_params', callback_params)
def test_callback_endpoint(extra_params, mocker):
    if 'fastapi' not in modules and util.find_spec('fastapi') is None:
        return

    from fastapi import FastAPI  # type: ignore[import]
    from fastapi.testclient import TestClient  # type: ignore[import]

    from spylib.oauth.fastapi import init_oauth_router

    app = FastAPI()

    oauth_router = init_oauth_router(**TEST_DATA)

    app.include_router(oauth_router)
    client = TestClient(app)

    is_login = extra_params['code'] == 'LOGINCODE'
    oauthjwt = OAuthJWT(
        is_login=is_login, storename=domain_to_storename(TEST_STORE), nonce=12345678990
    )
    state = oauthjwt.encode_token(key=TEST_DATA.private_key)
    jsondata = ONLINETOKEN_DATA if is_login else OFFLINETOKEN_DATA

    # Callback calls to get tokens
    shopify_request_mock = mocker.patch('httpx.AsyncClient.request', new_callable=AsyncMock)
    shopify_request_mock.side_effect = [
        MockHTTPResponse(status_code=200, jsondata=jsondata),
    ]

    params = dict(shop=TEST_STORE, state=state, timestamp=now_epoch())
    params.update(extra_params)
    query_str = build_callback_query_str(
        params=params,
        hmac_secret=SHOPIFY_SECRET_KEY,
        safe='=,&/[]:',
    )

    response = client.get('/callback', params=query_str, allow_redirects=False)
    assert response.status_code == 307
