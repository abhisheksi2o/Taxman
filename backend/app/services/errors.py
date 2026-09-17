class ServiceError(Exception):
    """Transport-agnostic error: routers map it to HTTPException, the browser backend to a JSON status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
