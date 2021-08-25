from typing import Any, Dict
from pydantic import validator
from pydantic.dataclasses import dataclass
from spylib import Store


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


class TestStore(Store):

    _online_tokens: Dict[str, Any]
    _offline_tokens: Dict[str, Any]

    def save_offline_token(self, store_name: str, key: str):
        TestStore._offline_tokens[store_name] = key

    def save_online_token(self, store_name: str, key: str):
        TestStore._online_tokens[store_name] = key

    def load_offline_token(self, store_name: str, key: str):
        return TestStore._offline_tokens[store_name]

    def load_online_token(self, store_name: str, key: str):
        return TestStore._online_tokens[store_name]
