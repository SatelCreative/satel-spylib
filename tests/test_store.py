from time import monotonic
from unittest.mock import AsyncMock

import pytest
from pydantic import validator
from pydantic.dataclasses import dataclass

from spylib import Store
from spylib.exceptions import ShopifyCallInvalidError, ShopifyExceedingMaxCostError


@dataclass
class MockHTTPResponse:
    status_code: int
    jsondata: dict
    headers: dict = None  # type: ignore

    @validator('headers', pre=True, always=True)
    def set_id(cls, fld):
        return fld or {'X-Shopify-Shop-Api-Call-Limit': '39/40'}

    def json(self):
        return self.jsondata


@pytest.mark.asyncio
async def test_store_rest_happypath(mocker):
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata={'success': True}),
    )

    jsondata = await store.shoprequest(
        goodstatus=200, debug='Test failed', endpoint='/test.json', method='get'
    )

    shopify_request_mock.assert_called_once()

    assert jsondata == {'success': True}

    # 80 from assuming Shopify plus then 1 used just now.
    assert store.tokens == 79


@pytest.mark.asyncio
async def test_store_rest_badrequest(mocker):
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(
            status_code=422, jsondata={'errors': {'title': ["can't be blank"]}}
        ),
    )

    with pytest.raises(ShopifyCallInvalidError):
        await store.shoprequest(
            goodstatus=201,
            debug='Test failed',
            endpoint='/products.json',
            method='post',
            json={'product': {'body_html': 'A mystery!'}},
        )

    shopify_request_mock.assert_called_once()


params = [
    pytest.param(0, 1000, 79, id='Last call hit rate limit, long time ago'),
    pytest.param(0, 20, 79, id='Last call hit rate limit, 20s ago'),
    pytest.param(0, 10, 39, id='Last call hit rate limit, 10s ago'),
    # Wait 1 second to get 4 then use 1 so 3
    pytest.param(0, 0, 3, id='Last call that hit rate limit just happened'),
]


@pytest.mark.parametrize('init_tokens, time_passed, expected_tokens', params)
@pytest.mark.asyncio
async def test_store_rest_ratetokens(init_tokens, time_passed, expected_tokens, mocker):
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

    # Simulate that there is only 2 calls available before hitting the rate limit.
    # If we set this to zero, then the code will wait 1 sec which is not great to keep the tests
    # fast
    store.tokens = init_tokens
    store.updated_at = monotonic() - time_passed  # A looooong time ago

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata={'success': True}),
    )
    await store.shoprequest(
        goodstatus=200, debug='Test failed', endpoint='/test.json', method='get'
    )

    shopify_request_mock.assert_called_once()

    assert store.tokens == expected_tokens


@pytest.mark.asyncio
async def test_store_graphql_happypath(mocker):
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

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

    jsondata = await store.execute_gql(query=query)

    shopify_request_mock.assert_called_once()

    assert jsondata == data


@pytest.mark.asyncio
async def test_store_graphql_badquery(mocker):
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

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
        await store.execute_gql(query=query)

    shopify_request_mock.assert_called_once()


@pytest.mark.asyncio
async def test_store_graphql_tokeninvalid(mocker):
    store = Store(store_id='TEST', name='test-store', access_token='INVALID')

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
        await store.execute_gql(query=query)

    shopify_request_mock.assert_called_once()


graphql_throttling_queries = [
    """
    {
      products(first: 10) {
        edges {
          node {
            variants(first: 96) {
              edges {
                node {
                  id
                }
              }
            }
          }
        }
      }
    }
    """,
    """
    {
      products(first: 10) {
        edges {
          node {
            variants(first: 100) {
              edges {
                node {
                  id
                }
              }
            }
          }
        }
      }
    }
    """,
]

params = [
    pytest.param(graphql_throttling_queries[0], 1000, 992, 42, id='Successful run'),
]


@pytest.mark.parametrize('query, currently_available, requested_cost, actual_cost', params)
@pytest.mark.asyncio
async def test_store_graphql_throttling_happypath(
    query, currently_available, requested_cost, actual_cost, mocker
):
    """
    Tests the throttling of the graphQL requests. There are 3 possible outcomes
    when running a test in regards to the throttling:

    1. The query runs with no issues (under the limit and within the current resources).
    2. The query fails to run due to a lack of resources, and needs to wait for
        the bucket to re-fill.
    3. The query fails indefinitely due to it being in excess of the maximum
        possible query size.

    This handles the first case (#1).
    """
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

    gql_response = {
        'extensions': {
            'cost': {
                'requestedQueryCost': requested_cost,
                'actualQueryCost': actual_cost,
                'throttleStatus': {
                    'maximumAvailable': 1000,
                    'currentlyAvailable': currently_available - actual_cost,
                    'restoreRate': 50,
                },
            }
        },
        'data': {
            'products': {
                'edges': [
                    {
                        'node': {
                            'variants': {
                                'edges': [
                                    {'node': {'id': 'gid://shopify/ProductVariant/19523123216406'}}
                                ]
                            }
                        }
                    }
                ]
            }
        },
    }

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata=gql_response),
    )
    await store.execute_gql(query=query)

    assert shopify_request_mock.call_count == 1


params = [
    pytest.param(graphql_throttling_queries[0], 800, 992, None, id='Failed run with sleep to fix'),
]


@pytest.mark.parametrize('query, currently_available, requested_cost, actual_cost', params)
@pytest.mark.asyncio
async def test_store_graphql_throttling_catch_cap(
    query, currently_available, requested_cost, actual_cost, mocker
):
    """
    Tests the throttling of the graphQL requests. There are 3 possible outcomes
    when running a test in regards to the throttling. This test covers:

    2. The query fails to run due to a lack of resources, and needs to wait for
        the bucket to re-fill.

    """
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

    gql_failure = {
        'extensions': {
            'cost': {
                'requestedQueryCost': requested_cost,
                'actualQueryCost': actual_cost,
                'throttleStatus': {
                    'maximumAvailable': 1000,
                    'currentlyAvailable': currently_available,
                    'restoreRate': 50,
                },
            }
        },
        'errors': [
            {
                'message': 'Throttled',
                'extensions': {
                    'code': 'THROTTLED',
                    'documentation': 'https://help.shopify.com/api/usage/rate-limits',
                },
            }
        ],
    }

    gql_success = {
        'extensions': {
            'cost': {
                'requestedQueryCost': requested_cost,
                'actualQueryCost': actual_cost,
                'throttleStatus': {
                    'maximumAvailable': 1000,
                    'currentlyAvailable': currently_available,
                    'restoreRate': 50,
                },
            }
        },
        'data': {
            'products': {
                'edges': [
                    {
                        'node': {
                            'variants': {
                                'edges': [
                                    {'node': {'id': 'gid://shopify/ProductVariant/19523123216406'}}
                                ]
                            }
                        }
                    }
                ]
            }
        },
    }

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        side_effect=[
            MockHTTPResponse(status_code=200, jsondata=gql_failure),
            MockHTTPResponse(status_code=200, jsondata=gql_success),
        ],
    )

    await store.execute_gql(query=query)

    assert shopify_request_mock.call_count == 2


params = [
    pytest.param(
        graphql_throttling_queries[1],
        id='Indefinite failed run',
    ),
]


@pytest.mark.parametrize('query', params)
@pytest.mark.asyncio
async def test_store_graphql_throttling_error_test(query, mocker):
    """
    Tests the throttling of the graphQL requests. There are 3 possible outcomes
    when running a test in regards to the throttling, this handles

    3. The query fails indefinitely due to it being in excess of the maximum
        possible query size.
    """
    store = Store(store_id='TEST', name='test-store', access_token='Te5tM3')

    gql_response = {
        'errors': [
            {
                'message': 'Query cost is 1032, which exceeds the single query ',
                'extensions': {
                    'code': 'MAX_COST_EXCEEDED',
                    'cost': 1032,
                    'maxCost': 1000,
                    'documentation': 'https://help.shopify.com/api/usage/rate-limits',
                },
            }
        ]
    }

    shopify_request_mock = mocker.patch(
        'httpx.AsyncClient.request',
        new_callable=AsyncMock,
        return_value=MockHTTPResponse(status_code=200, jsondata=gql_response),
    )

    with pytest.raises(ShopifyExceedingMaxCostError):
        await store.execute_gql(query=query)

    assert shopify_request_mock.call_count == 1
