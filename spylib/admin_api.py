import logging
from abc import ABC, abstractmethod
from asyncio import sleep
from datetime import datetime, timedelta
from json.decoder import JSONDecodeError
from math import ceil, floor
from time import monotonic
from typing import Annotated, Any, ClassVar, Dict, List, NoReturn, Optional

from httpx import AsyncClient, Response
from pydantic import BaseModel, BeforeValidator, ConfigDict
from starlette import status
from tenacity import retry
from tenacity.retry import retry_if_exception, retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_random

from spylib.constants import (
    API_CALL_NUMBER_RETRY_ATTEMPTS,
    MAX_COST_EXCEEDED_ERROR_CODE,
    OPERATION_NAME_REQUIRED_ERROR_MESSAGE,
    THROTTLED_ERROR_CODE,
    WRONG_OPERATION_NAME_ERROR_MESSAGE,
)
from spylib.exceptions import (
    ShopifyCallInvalidError,
    ShopifyError,
    ShopifyExceedingMaxCostError,
    ShopifyGQLError,
    ShopifyIntermittentError,
    ShopifyInvalidResponseBody,
    ShopifyThrottledError,
    not_our_fault,
)
from spylib.utils.misc import parse_scope
from spylib.utils.rest import Request


class Token(ABC, BaseModel):
    """Abstract class for token objects.

    This should never be extended, as you should either be
    using the OfflineTokenABC or the OnlineTokenABC.
    """

    store_name: str
    scope: Annotated[List[str], BeforeValidator(parse_scope)] = []
    access_token: Optional[str] = None
    access_token_invalid: bool = False

    api_version: ClassVar[Optional[str]] = None

    rest_bucket_max: int = 80
    rest_bucket: int = rest_bucket_max
    rest_leak_rate: int = 4

    graphql_bucket_max: int = 1000
    graphql_bucket: int = graphql_bucket_max
    graphql_leak_rate: int = 50

    updated_at: float = monotonic()

    client: ClassVar[AsyncClient] = AsyncClient()

    @property
    def oauth_url(self) -> str:
        return f'https://{self.store_name}.myshopify.com/admin/oauth/access_token'

    @property
    def api_url(self) -> str:
        if not self.api_version:
            return f'https://{self.store_name}.myshopify.com/admin'
        return f'https://{self.store_name}.myshopify.com/admin/api/{self.api_version}'

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Methods for querying the store

    async def __await_rest_bucket_refill(self):
        self.__fill_rest_bucket()
        while self.rest_bucket <= 1:
            await sleep(1)
            self.__fill_rest_bucket()
        self.rest_bucket -= 1

    def __fill_rest_bucket(self):
        now = monotonic()
        time_since_update = now - self.updated_at
        new_tokens = floor(time_since_update * self.rest_leak_rate)
        if new_tokens > 1:
            self.rest_bucket = min(self.rest_bucket + new_tokens, self.rest_bucket_max)
            self.updated_at = now

    async def __handle_error(self, debug: str, endpoint: str, response: Response):
        """Handle any error that occured when calling Shopify.

        If the response has a valid json then return it too.
        """
        msg = (
            f'ERROR in store {self.store_name}: {debug}\n'
            f'API response code: {response.status_code}\n'
            f'API endpoint: {endpoint}\n'
        )
        try:
            jresp = response.json()
        except Exception:
            pass
        else:
            msg += f'API response json: {jresp}\n'

        if 400 <= response.status_code < 500:
            # This appears to be our fault
            raise ShopifyCallInvalidError(msg)

        raise ShopifyError(msg)

    @retry(
        reraise=True,
        wait=wait_random(min=1, max=2),
        stop=stop_after_attempt(API_CALL_NUMBER_RETRY_ATTEMPTS),
        retry=retry_if_exception(not_our_fault),
    )
    async def execute_rest(
        self,
        request: Request,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        debug: str = '',
    ) -> Dict[str, Any]:
        while True:
            await self.__await_rest_bucket_refill()

            if not self.access_token:
                raise ValueError('You have not initialized the token for this store. ')

            response = await self.client.request(
                method=request.method.value,
                url=f'{self.api_url}{endpoint}',
                headers={'X-Shopify-Access-Token': self.access_token},
                json=json,
            )
            if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                # We hit the limit, we are out of tokens
                self.rest_bucket = 0
                continue
            elif 400 <= response.status_code or response.status_code != request.good_status:
                # All errors are handled here
                await self.__handle_error(debug=debug, endpoint=endpoint, response=response)
            else:
                jresp = response.json()
                # Recalculate the rate to be sure we have the right one.
                calllimit = response.headers['X-Shopify-Shop-Api-Call-Limit']
                self.rest_bucket_max = int(calllimit.split('/')[1])
                # In Shopify the bucket is emptied after 20 seconds
                # regardless of the bucket size.
                self.rest_leak_rate = int(self.rest_bucket_max / 20)

            return jresp

    @retry(
        reraise=True,
        stop=stop_after_attempt(API_CALL_NUMBER_RETRY_ATTEMPTS),
        retry=retry_if_exception_type(
            (ShopifyThrottledError, ShopifyInvalidResponseBody, ShopifyIntermittentError)
        ),
    )
    async def execute_gql(
        self,
        query: str,
        variables: Dict[str, Any] = {},
        operation_name: Optional[str] = None,
        suppress_errors: bool = False,
    ) -> Dict[str, Any]:
        if not self.access_token:
            raise ValueError('Token Undefined')

        url = f'{self.api_url}/graphql.json'

        headers = {
            'Content-type': 'application/json',
            'X-Shopify-Access-Token': self.access_token,
        }

        body = {'query': query, 'variables': variables, 'operationName': operation_name}

        resp = await self.client.post(
            url=url,
            json=body,
            headers=headers,
        )

        jsondata = await self._check_for_errors(
            response=resp, suppress_errors=suppress_errors, operation_name=operation_name
        )

        return jsondata['data']

    async def _check_for_errors(
        self, response, suppress_errors: bool, operation_name: str | None
    ) -> dict:
        # Handle any response that is not 200, which will return with error message
        # https://shopify.dev/api/admin-graphql#status_and_error_codes
        if response.status_code != 200:
            self._handle_non_200_status_codes(response=response)

        jsondata = self._extract_jsondata_from(response=response)

        errors: list | str = jsondata.get('errors', [])
        if errors:
            has_data_field = 'data' in jsondata
            if has_data_field and not suppress_errors:
                raise ShopifyGQLError(jsondata)

            if isinstance(errors, str):
                self._handle_invalid_access_token(errors)
                raise ShopifyGQLError(f'Unknown errors string: {jsondata}')

            await self._handle_errors_list(
                jsondata=jsondata, errors=errors, operation_name=operation_name
            )

            errorlist = '\n'.join(
                [err['message'] for err in jsondata['errors'] if 'message' in err]
            )
            raise ValueError(f'GraphQL query is incorrect:\n{errorlist}')

        return jsondata

    def _handle_non_200_status_codes(self, response) -> NoReturn:
        if response.status_code in [500, 503]:
            raise ShopifyIntermittentError(
                f'The Shopify API returned an intermittent error: {response.status_code}.'
            )

        try:
            jsondata = response.json()
            error_msg = f'{response.status_code}. {jsondata["errors"]}'
        except JSONDecodeError:
            error_msg = f'{response.status_code}.'

        raise ShopifyGQLError(f'GQL query failed, status code: {error_msg}')

    @staticmethod
    def _extract_jsondata_from(response) -> dict:
        try:
            jsondata = response.json()
        except JSONDecodeError as exc:
            raise ShopifyInvalidResponseBody from exc

        if not isinstance(jsondata, dict):
            raise ValueError('JSON data is not a dictionary')

        return jsondata

    def _handle_invalid_access_token(self, errors: str) -> None:
        if 'Invalid API key or access token' in errors:
            self.access_token_invalid = True
            logging.warning(
                f'Store {self.store_name}: The Shopify API token is invalid. '
                'Flag the access token as invalid.'
            )
            raise ConnectionRefusedError

    async def _handle_errors_list(
        self, jsondata: dict, errors: list, operation_name: str | None
    ) -> None:
        # Only report on the first error just to simplify: We will raise an exception anyway.
        err = errors[0]

        if 'extensions' in err and 'code' in err['extensions']:
            error_code = err['extensions']['code']
            self._handle_max_cost_exceeded_error_code(error_code=error_code)
            await self._handle_throttled_error_code(error_code=error_code, jsondata=jsondata)

        if 'message' in err:
            self._handle_operation_name_required_error(error_message=err['message'])
            self._handle_wrong_operation_name_error(
                error_message=err['message'], operation_name=operation_name
            )

    def _handle_max_cost_exceeded_error_code(self, error_code: str) -> None:
        if error_code != MAX_COST_EXCEEDED_ERROR_CODE:
            return

        raise ShopifyExceedingMaxCostError(
            f'Store {self.store_name}: This query was rejected by the Shopify'
            f' API, and will never run as written, as the query cost'
            f' is larger than the max possible query size (>{self.graphql_bucket_max})'
            ' for Shopify.'
        )

    async def _handle_throttled_error_code(self, error_code: str, jsondata: dict) -> None:
        if error_code != THROTTLED_ERROR_CODE:
            return

        cost = jsondata['extensions']['cost']
        query_cost = cost['requestedQueryCost']
        available = cost['throttleStatus']['currentlyAvailable']
        rate = cost['throttleStatus']['restoreRate']
        sleep_time = ceil((query_cost - available) / rate)
        await sleep(sleep_time)
        raise ShopifyThrottledError

    def _handle_operation_name_required_error(self, error_message: str) -> None:
        if error_message != OPERATION_NAME_REQUIRED_ERROR_MESSAGE:
            return

        raise ShopifyCallInvalidError(
            f'Store {self.store_name}: Operation name was required for this query.'
            'This likely means you have multiple queries within one call '
            'and you must specify which to run.'
        )

    def _handle_wrong_operation_name_error(
        self, error_message: str, operation_name: str | None
    ) -> None:
        if error_message != WRONG_OPERATION_NAME_ERROR_MESSAGE.format(operation_name):
            return

        raise ShopifyCallInvalidError(
            f'Store {self.store_name}: Operation name {operation_name}'
            'does not exist in the query.'
        )


class OfflineTokenABC(Token, ABC):
    """Offline tokens are used for long term access, and do not have a set expiry."""

    @abstractmethod
    async def save(self):
        pass

    @classmethod
    @abstractmethod
    async def load(cls, store_name: str):
        pass


class OnlineTokenABC(Token, ABC):
    """Online tokens are used to implement applications authenticated with a specific user's credentials.

    These extend on the original token, adding in a user, its scope and an expiry time.
    """

    associated_user_id: int
    expires_in: int = 0
    expires_at: datetime = datetime.now() + timedelta(days=0, seconds=expires_in)

    @abstractmethod
    async def save(self):
        """This method handles saving the token.

        By default this does nothing, therefore the developer should override this.
        """

    @classmethod
    @abstractmethod
    async def load(cls, store_name: str, associated_user: str):
        """This method handles loading the token.

        By default this does nothing, therefore the developer should override this.
        """


class PrivateTokenABC(Token, ABC):
    """Private token implementation, when we are pulling this from the config file.

    Therefore we do not need the save function for the token class as there is
    no calls to the OAuth endpoints for shopify.
    """

    @classmethod
    @abstractmethod
    async def load(cls, store_name: str):
        """This method handles loading the token.

        By default this does nothing, therefore the developer should override this.
        """
        pass
