from unittest.mock import AsyncMock

import pytest

from ...shared import MockHTTPResponse, OnlineToken, store_name, online_token_data


@pytest.mark.asyncio
async def test_store_graphql_happypath(mocker):
    token = OnlineToken(
        store_name=store_name,
        access_token=online_token_data.access_token,
        scope=online_token_data.scope.split(','),
        expires_in=online_token_data.expires_in,
        associated_user_scope=online_token_data.associated_user_scope.split(','),
        associated_user=online_token_data.associated_user,
    )

    query = '''
    {
      shop {
        name
      }
    }'''
    data = {'shop': {'name': 'graphql-admin'}}
    gql_response = {
        'data': data,
        'extensions': {
            'cost': {
                'requestedQueryCost': 1,
                'actualQueryCost': 1,
                'throttleStatus': {
                    'maximumAvailable': 1000,
                    'currentlyAvailable': 999,
                    'restoreRate': 50,
                },
            }
        },
    }

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata=gql_response),
    )

    jsondata = await token.execute_gql(query=query)

    shopify_request_mock.assert_called_once()

    assert jsondata == data


@pytest.mark.asyncio
async def test_store_graphql_badquery(mocker):
    token = OnlineToken(
        store_name=store_name,
        access_token=online_token_data.access_token,
        scope=online_token_data.scope.split(','),
        expires_in=online_token_data.expires_in,
        associated_user_scope=online_token_data.associated_user_scope.split(','),
        associated_user=online_token_data.associated_user,
    )

    query = '''
    {
      shopp {
        name
      }
    }'''
    error_msg = "Field 'shopp' doesn't exist on type 'QueryRoot'"
    gql_response = {
        'errors': [
            {
                'message': error_msg,
                'locations': [{'line': 2, 'column': 3}],
                'path': ['query', 'shopp'],
                'extensions': {
                    'code': 'undefinedField',
                    'typeName': 'QueryRoot',
                    'fieldName': 'shopp',
                },
            }
        ]
    }

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata=gql_response),
    )

    with pytest.raises(ValueError, match=f'^GraphQL query is incorrect:\n{error_msg}$'):
        await token.execute_gql(query=query)

    shopify_request_mock.assert_called_once()


@pytest.mark.asyncio
async def test_store_graphql_tokeninvalid(mocker):
    token = OnlineToken(
        store_name=store_name,
        access_token=online_token_data.access_token,
        scope=online_token_data.scope.split(','),
        expires_in=online_token_data.expires_in,
        associated_user_scope=online_token_data.associated_user_scope.split(','),
        associated_user=online_token_data.associated_user,
    )

    query = '''
    {
      shop {
        name
      }
    }'''
    gql_response = {
        'errors': '[API] Invalid API key or access token (unrecognized login or wrong password)'
    }

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata=gql_response),
    )

    with pytest.raises(ConnectionRefusedError):
        await token.execute_gql(query=query)

    shopify_request_mock.assert_called_once()
