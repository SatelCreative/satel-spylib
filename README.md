# Shopify Python Library - SPyLib

The Shopify python library or SPyLib, simplifies the use of the Shopify
services such as the REST and GraphQL APIs as well as the OAuth authentication.
All of this is done **asynchronously using asyncio**.

![Tests](https://github.com/SatelCreative/satel-spylib/actions/workflows/tests.yml/badge.svg)

## Installation

```bash
pip install spylib
```

## Contributing

If you want to contribute a small change/feature, the best is to just create a PR with
your changes.
For bigger changes/features it's best to open an issue first and discuss it to agree
on the code organization and overall implementation before spending too much time on
the code, unless you want to keep it in your own forked repo.

### Setting up the development environment

We use the [python poetry](https://python-poetry.org/) package to manage this package.
Follow the official instructions to install poetry on your system then once you clone
this repository just just need to do the following to install the dependencies from
the development environment, as well as install `spylib` in
[editable mode](https://pip.pypa.io/en/stable/cli/pip_install/#install-editable):
```bash
poetry install
```

Then you can start monitoring the code for changes and run the test suite this way:
```bash
poetry shell
scripts/test_watch.sh
```


## Overview

### Token

The token class contains the majority of the logic for communicating with shopify.
To use the token class, you must define a child class for the tokens you are using 
and implement the `save` and `load` abstract methods. Your option for child classes 
are `OnlineTokenABC` or `OfflineTokenABC`:

#### Implement Token Classes 

```python
class OfflineToken(OfflineTokenABC):
  async def save(self):
      # Some code to save the token to a database

  @classmethod
  async def load(cls, store_name: str):
      # Some code to load the token from the database

class OnlineToken(OnlineTokenABC):
  async def save(self):
      # Some code to save the token to a database

  @classmethod
  async def load(cls, store_name: str, user_id: str):
      # Some code to load the token from the database
```

#### Create Token

Once you have defined these methods, we can create an instance of a token using
one of the following:

```python
token = OfflineToken(
  store_name,
  access_token,
  scope
)

token = OnlineToken(
  store_name,
  access_token,
  scope,
  expires_in,
  associated_user_scope,
  associated_user_id
)
```

### Querying Shopify

#### REST

We can query the store using either the REST:

```python
token.execute_rest(
  request: Request,
  endpoint: str,
  json: Optional[Dict[str, Any]],
  debug: Optional[str],
)
```

For example, if you want to query a product from shopify you can run:

```python
token.execute_rest(
  request: GET,
  endpoint: f'/products/{product_id}.json'
)
```

If you want to update a product in a shop you can run:

```python
token.execute_rest(
  request: PUT,
  endpoint: f'/products/{product_id}.json',
  json: {
    "product":
    {
      "id": product_id,
      "title": "New Title"
    }
  }
)
```

The `REST` method takes a `request` parameter which is one of the `Request` constants defined in 
the [rest](./spylib/utils/rest.py) file. The options are `GET`, `POST`, `PUT`, or `DELETE`.

Endpoint is the API endpoint string that we are querying, this should be similar to 
the following format:

```python
f'/{resource}.json?fields={resource.param}&{[params]}'
```

The `debug` parameter is the message that is returned when there is an error. It is optional as it defaults to `""`.

#### GQL

We can also query Shopify using the GraphQL endpoint:

```python
token.execute_gql(
  query,
  variables,
  operation_name
)
```

### REST Types

### OAuth

**Notice** there have been considerable changes to the oauth in version 0.3. The core
of this change has been the move from environment variables to parameters in the init
script for the routers. The following describes a very basic implementation.

Rather than reimplementing for each app the
[Shopify OAuth authentication](https://shopify.dev/tutorials/authenticate-with-oauth)
one can simple get a [FastAPI](https://fastapi.tiangolo.com/) router that provides
the install and callback endpoints ready to handle the whole OAuth process.
You just need to call `init_oauth_router` such that:

```python
from spylib.oauth import OfflineToken, OnlineToken, init_oauth_router


async def my_post_install(storename: str, offline_token: OfflineToken):
    """Function handling the offline token obtained at the end of the installation"""
    # Store to database
    pass

async def my_post_login(storename: str, online_token: OnlineToken):
    """Function handling the online token obtained at the end of the user login"""
    # Store to database
    pass

oauth_router = init_oauth_router(
    app_scopes=['write_orders', 'write_products'],
    user_scopes=['read_orders', 'write_products'],
    public_domain='my.app.com',
    private_key='KEY_FOR_OAUTH_JWT',
    api_key='SHOPIFY_APP_API_KEY',
    api_secret_key='SHOPIFY_APP_SECRET_KEY',
    app_handle='SHOPIFY_APP_HANDLE',
    post_install=my_post_install,
    post_login=my_post_login,
)
```

The `app_scopes` are for the offline token and the `user_scopes` for the online token.
The `public_domain` is used to set the callback URL used in the OAuth process.

This library uses a JWT encoded `nonce` to avoid the need for a database or some other
mechanism to track the `nonce`. This JWT has an expiration time and is unique for each
OAuth process making it a valid `nonce` mechanism.
The `private_key` parameter defines the key used to encode and decode this JWT.


The api and secret key can be found inside your shopify app main configuration page. The
app handle can be found in the same spot but needs to be pulled from the url:

1. Go to your shopify app's editing page (The url should be `https://partners.shopify.com/<partner_id>/apps/<app_id>/edit`)
2. Open the console and run `window.RailsData.user.app.handle`. The result is the handle.

The `post_install` and `post_login` provide a way to inject functions handling the
result of the installation and the login processes respectivaly. They are meant in 
particular to record the offline and online tokens in your app's database.
They can be synchronous or asynchronous functions taking the storename and the token
as arguments.

### Session Tokens

The [session token](https://shopify.dev/apps/auth/session-tokens/authenticate-an-embedded-app-using-session-tokens) 
functionality can be used to verify the session for the user. The suggested syntax is to define a partial function:

```python
from spylib.utils import SessionToken

decode_session_token = partial(SessionToken.decode_token_from_header, api_key=api_key, secret=secret)
```

Then this can be used as a dependency in FastAPI by doing the following:

```python
@app.get("/items/")
async def read_items(session: SessionToken = Depends(decode_session_token)):
  # Some api code
```

## Maintenance

We use [poetry](https://python-poetry.org/) to manage the dependencies and
[flit](https://flit.readthedocs.io/en/latest/index.html) to build and publish to pypi
because unlike poetry it allows to set the metadata on pypi such as author or homepage.

### How to publish

1. Change the version in the `pyproject.toml` and `spylib/__init__.py` files
    * you can use `poetry version XXXXX` to change `pyproject.toml`
2. Commit to git
3. Run `poetry build` to create the package folders in `/dist`
4. Run `flit publish` to publish to PyPI
5. Tag the release in git and push it to Github

**Notes**:
* It's better to tag after publishing in case there is an issue while publishing
* `flit` will try to use the system's keyring if the keyring package is installed.
  Run `flit` in a virtual environment without keyring if you prefer to bypass the
  keyring and put your password whenever you publish
