"""A geocoder with fixed answers, for tests: no network, and a record of what it was asked."""

from worst_route.geocode.base import Bbox, Candidate


class FakeGeocoder:
    """Answers each query from `answers` (nothing for a query it does not have), at most
    `limit` candidates; `calls` lists the queries, in order."""

    def __init__(self, answers: dict[str, list[Candidate]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    async def search(self, query: str, bbox: Bbox, limit: int) -> list[Candidate]:
        self.calls.append(query)
        return self.answers.get(query, [])[:limit]

    async def aclose(self) -> None:
        pass
