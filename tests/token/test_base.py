from unittest.mock import AsyncMock
import pytest
from .shared import (
    MockHTTPResponse,
    OfflineToken,
    OnlineToken,
    store_name,
    api_version,
    client_id,
    client_secret,
    code,
    database,
    offline_token_data,
    online_token_data,
)


@pytest.mark.asyncio
async def test_offline_token(mocker):
    # Create a new token
    offline_token = OfflineToken(
        store_name=store_name,
        access_token=offline_token_data.access_token,
        scope=offline_token_data.scope.split(','),
    )

    assert offline_token.access_token == offline_token_data.access_token
    assert offline_token.scope == offline_token_data.scope.split(',')
    assert offline_token.store_name == store_name
    assert not offline_token.access_token_invalid
    assert offline_token.api_url == f'https://{store_name}.myshopify.com/admin/api/{api_version}'
    assert (
        offline_token.oauth_url == f'https://{store_name}.myshopify.com/admin/oauth/access_token'
    )


@pytest.mark.asyncio
async def test_online_token(mocker):
    # Create a new token
    online_token = OnlineToken(
        store_name=store_name,
        access_token=online_token_data.access_token,
        scope=online_token_data.scope.split(','),
        expires_in=online_token_data.expires_in,
        associated_user_scope=online_token_data.associated_user_scope.split(','),
        associated_user=online_token_data.associated_user,
    )

    assert online_token.access_token == online_token_data.access_token
    assert not online_token.access_token_invalid
    assert online_token.scope == online_token_data.scope.split(',')
    assert online_token.associated_user == online_token_data.associated_user
    assert online_token.associated_user_scope == online_token_data.associated_user_scope.split(',')
    assert online_token.api_url == f'https://{store_name}.myshopify.com/admin/api/{api_version}'
    assert online_token.oauth_url == f'https://{store_name}.myshopify.com/admin/oauth/access_token'


@pytest.mark.asyncio
async def test_new_offline_token(mocker):
    """
    This tests the ability to generate a new token from the endpoint using the
    client_id, client_secret, and code.
    """
    # Generating the object callback with the token information
    shopify_request_mock = mocker.patch('httpx.AsyncClient.request', new_callable=AsyncMock)
    shopify_request_mock.side_effect = [
        MockHTTPResponse(status_code=200, jsondata=offline_token_data),
    ]

    # Create a new token
    offline_token = await OfflineToken.new(
        store_name,
        client_id,
        client_secret,
        code,
    )

    assert offline_token.access_token == offline_token_data.access_token
    assert offline_token.scope == offline_token_data.scope.split(',')
    assert offline_token.store_name == store_name
    assert not offline_token.access_token_invalid
    assert offline_token.api_url == f'https://{store_name}.myshopify.com/admin/api/{api_version}'
    assert (
        offline_token.oauth_url == f'https://{store_name}.myshopify.com/admin/oauth/access_token'
    )


@pytest.mark.asyncio
async def test_new_online_token(mocker):
    """
    This tests the ability to generate a new token from the endpoint using the
    client_id, client_secret, and code.
    """
    # Generating the object callback with the token information
    shopify_request_mock = mocker.patch('httpx.AsyncClient.request', new_callable=AsyncMock)
    shopify_request_mock.side_effect = [
        MockHTTPResponse(status_code=200, jsondata=online_token_data),
    ]

    # Create a new token
    online_token = await OnlineToken.new(
        store_name,
        client_id,
        client_secret,
        code,
    )

    assert online_token.access_token == online_token_data.access_token
    assert not online_token.access_token_invalid
    assert online_token.scope == online_token_data.scope.split(',')
    assert online_token.associated_user == online_token_data.associated_user
    assert online_token.associated_user_scope == online_token_data.associated_user_scope.split(',')
    assert online_token.api_url == f'https://{store_name}.myshopify.com/admin/api/{api_version}'
    assert online_token.oauth_url == f'https://{store_name}.myshopify.com/admin/oauth/access_token'


@pytest.mark.asyncio
async def test_save_offline_token(mocker):
    # Create a new token
    offline_token = OfflineToken(
        store_name=store_name,
        access_token=offline_token_data.access_token,
        scope=offline_token_data.scope.split(','),
    )

    await offline_token.save_token()

    new_offline_token = database['offline'][offline_token.store_name]

    # Check to see if the saved token is the same as the original
    assert new_offline_token == offline_token


@pytest.mark.asyncio
async def test_save_online_token(mocker):

    # Create a new token
    online_token = OnlineToken(
        store_name=store_name,
        access_token=online_token_data.access_token,
        scope=online_token_data.scope.split(','),
        expires_in=online_token_data.expires_in,
        associated_user_scope=online_token_data.associated_user_scope.split(','),
        associated_user=online_token_data.associated_user,
    )

    await online_token.save_token()

    new_online_token = database['online'][online_token.store_name][online_token.associated_user.id]

    # Check to see if the saved token is the same as the original
    assert new_online_token == online_token


@pytest.mark.asyncio
async def test_load_offline_token(mocker):
    # Create a new token
    offline_token = OfflineToken(
        store_name=store_name,
        access_token=offline_token_data.access_token,
        scope=offline_token_data.scope.split(','),
    )

    database['offline'][store_name] = offline_token

    new_offline_token = await offline_token.load_token(store_name)

    # Check to see if the loaded token is the same as the original
    assert new_offline_token == offline_token


@pytest.mark.asyncio
async def test_load_online_token(mocker):

    # Create a new token
    online_token = OnlineToken(
        store_name=store_name,
        access_token=online_token_data.access_token,
        scope=online_token_data.scope.split(','),
        expires_in=online_token_data.expires_in,
        associated_user_scope=online_token_data.associated_user_scope.split(','),
        associated_user=online_token_data.associated_user,
    )

    database['online'][store_name] = {}
    database['online'][store_name][online_token.associated_user.id] = online_token

    new_online_token = await online_token.load_token(
        store_name, online_token_data.associated_user.id
    )

    # Check to see if the saved token is the same as the original
    assert new_online_token == online_token
