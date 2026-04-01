"""Tests for the ReportPortal HTTP client."""

import httpx
import pytest
import respx

from rp_fetch.client import RPAuthError, RPClient, RPClientError, RPNotFoundError
from rp_fetch.models import Launch

BASE_URL = "https://rp.example.com"
PROJECT = "test_project"
API_KEY = "test-key"

LAUNCH_RESPONSE = {
    "id": 1,
    "uuid": "abc-123",
    "name": "Test Launch",
    "status": "PASSED",
    "startTime": "2026-03-18T14:00:00Z",
    "endTime": "2026-03-18T15:00:00Z",
    "attributes": [],
    "statistics": {},
}

LAUNCHES_LIST_RESPONSE = {
    "content": [LAUNCH_RESPONSE],
    "page": {
        "number": 1,
        "size": 20,
        "totalElements": 1,
        "totalPages": 1,
    },
}

ITEMS_RESPONSE = {
    "content": [
        {
            "id": 10,
            "uuid": "item-uuid-1",
            "name": "Test Suite",
            "type": "SUITE",
            "status": "PASSED",
            "launchId": 1,
            "hasChildren": True,
            "hasStats": True,
        }
    ],
    "page": {"number": 1, "size": 100, "totalElements": 1, "totalPages": 1},
}

LOGS_RESPONSE = {
    "content": [
        {
            "id": 100,
            "uuid": "log-uuid-1",
            "message": "Test passed",
            "level": "INFO",
            "logTime": "2026-03-18T14:30:00Z",
            "itemId": 10,
        }
    ],
    "page": {"number": 1, "size": 100, "totalElements": 1, "totalPages": 1},
}


@pytest.fixture
def client():
    return RPClient(BASE_URL, API_KEY, PROJECT)


@pytest.mark.asyncio
@respx.mock
async def test_get_launch(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch/uuid/abc-123").mock(
        return_value=httpx.Response(200, json=LAUNCH_RESPONSE)
    )
    async with client:
        launch = await client.get_launch("abc-123")
    assert launch.name == "Test Launch"
    assert launch.uuid == "abc-123"
    assert launch.status == "PASSED"


@pytest.mark.asyncio
@respx.mock
async def test_list_launches(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch").mock(
        return_value=httpx.Response(200, json=LAUNCHES_LIST_RESPONSE)
    )
    async with client:
        launches, page = await client.list_launches(limit=20)
    assert len(launches) == 1
    assert launches[0].name == "Test Launch"
    assert page.total_elements == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_items(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/item").mock(
        return_value=httpx.Response(200, json=ITEMS_RESPONSE)
    )
    async with client:
        items, page = await client.get_items(1)
    assert len(items) == 1
    assert items[0].name == "Test Suite"


@pytest.mark.asyncio
@respx.mock
async def test_get_logs(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/log").mock(
        return_value=httpx.Response(200, json=LOGS_RESPONSE)
    )
    async with client:
        logs, page = await client.get_logs(10)
    assert len(logs) == 1
    assert logs[0].message == "Test passed"


@pytest.mark.asyncio
@respx.mock
async def test_auth_error_401(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch").mock(
        return_value=httpx.Response(401)
    )
    async with client:
        with pytest.raises(RPAuthError, match="401"):
            await client.list_launches()


@pytest.mark.asyncio
@respx.mock
async def test_auth_error_403(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch").mock(
        return_value=httpx.Response(403)
    )
    async with client:
        with pytest.raises(RPAuthError, match="403"):
            await client.list_launches()


@pytest.mark.asyncio
@respx.mock
async def test_not_found_404(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch/uuid/bad-id").mock(
        return_value=httpx.Response(404)
    )
    async with client:
        with pytest.raises(RPNotFoundError):
            await client.get_launch("bad-id")


@pytest.mark.asyncio
@respx.mock
async def test_download_attachment(client):
    binary_data = b"\x89PNG\r\n\x1a\nfake-image-data"
    respx.get(f"{BASE_URL}/api/v1/data/{PROJECT}/bin-123").mock(
        return_value=httpx.Response(200, content=binary_data)
    )
    async with client:
        data = await client.download_attachment("bin-123")
    assert data == binary_data


@pytest.mark.asyncio
@respx.mock
async def test_download_attachment_407_raises_proxy_auth_error(client):
    respx.get(f"{BASE_URL}/api/v1/data/{PROJECT}/bin-456").mock(
        return_value=httpx.Response(407)
    )
    async with client:
        with pytest.raises(RPProxyAuthError, match="407"):
            await client.download_attachment("bin-456")


@pytest.mark.asyncio
@respx.mock
async def test_download_attachment_404_raises_not_found(client):
    respx.get(f"{BASE_URL}/api/v1/data/{PROJECT}/bin-missing").mock(
        return_value=httpx.Response(404)
    )
    async with client:
        with pytest.raises(RPNotFoundError):
            await client.download_attachment("bin-missing")


from rp_fetch.client import RPProxyAuthError


def test_client_stores_proxy_url():
    client = RPClient(BASE_URL, API_KEY, PROJECT, proxy_url="http://proxy:8080")
    assert client._proxy_url == "http://proxy:8080"


def test_client_proxy_url_defaults_to_none():
    client = RPClient(BASE_URL, API_KEY, PROJECT)
    assert client._proxy_url is None


def test_client_stores_proxy_headers():
    headers = {"Proxy-Authorization": "Bearer tok123"}
    client = RPClient(
        BASE_URL, API_KEY, PROJECT, proxy_url="http://p:80", proxy_headers=headers
    )
    assert client._proxy_headers == headers
    # Proxy-Authorization must NOT leak into regular request headers
    assert "Proxy-Authorization" not in client._headers


@pytest.mark.asyncio
@respx.mock
async def test_proxy_407_raises_proxy_auth_error():
    client = RPClient(BASE_URL, API_KEY, PROJECT)
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch").mock(
        return_value=httpx.Response(407)
    )
    async with client:
        with pytest.raises(RPProxyAuthError, match="407"):
            await client.list_launches()


@pytest.mark.asyncio
@respx.mock
async def test_network_error_raises_client_error(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch").mock(
        side_effect=httpx.ConnectError("DNS resolution failed")
    )
    async with client:
        with pytest.raises(RPClientError, match="Network error"):
            await client.list_launches()


@pytest.mark.asyncio
@respx.mock
async def test_http_500_raises_client_error(client):
    respx.get(f"{BASE_URL}/api/v1/{PROJECT}/launch").mock(
        return_value=httpx.Response(500)
    )
    async with client:
        with pytest.raises(RPClientError, match="500"):
            await client.list_launches()
