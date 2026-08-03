import httpx
from astra_memory import ModelBackedMemoryExtractor
from astra_model_providers import OpenAICompatibleLocalModelProvider


async def test_model_backed_extractor_validates_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["temperature"] == 0
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"kind":"preference","content":"brief answers",'
                                '"confidence":0.9,"promoted":true}]'
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        base_url="http://local-model.test", transport=httpx.MockTransport(handler)
    )
    provider = OpenAICompatibleLocalModelProvider(
        "http://local-model.test", "test-model", client=client
    )
    extracted = await ModelBackedMemoryExtractor(provider).extract("Please remember my style")
    assert extracted[0].content == "brief answers"
    assert extracted[0].promoted
    await client.aclose()


async def test_model_extractor_falls_back_on_invalid_output() -> None:
    class InvalidProvider:
        async def process(self, instruction: str, content: str) -> str:
            return "not json"

    extracted = await ModelBackedMemoryExtractor(InvalidProvider()).extract("I prefer concise text")
    assert extracted[0].content == "concise text"


async def test_model_extractor_accepts_json_wrapped_in_prose() -> None:
    class WrappedProvider:
        async def process(self, instruction: str, content: str) -> str:
            return (
                "Here is the extraction:\n```json\n"
                '[{"kind":"fact","content":"water plants nightly",'
                '"confidence":0.9,"promoted":true}]\n```'
            )

    extracted = await ModelBackedMemoryExtractor(WrappedProvider()).extract("Remember my plants")
    assert extracted[0].content == "water plants nightly"
