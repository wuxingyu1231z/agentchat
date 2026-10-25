from pydantic import BaseModel, Field, ConfigDict


class PdfPage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(ge=1, le=300)
    text: str = Field(max_length=2_000_000)


class PdfResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pages: list[PdfPage] = Field(max_length=300)
    total_pages: int = Field(ge=0)
    truncated: bool
