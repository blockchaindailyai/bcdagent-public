from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, HttpUrl

Severity = Literal["urgent","high","normal","low","info"]
Priority = Literal["breaking","normal"]
AssetClass = Literal["general","crypto","stocks","bonds","rates","commodities","fx","credit"]
Region = Literal["US","EU","JP","KR","CN","UK"]

class IntelClassificationV1(BaseModel):
    priority: Optional[Priority] = None
    assetClass: Optional[AssetClass] = None
    subcategory: Optional[AssetClass] = None
    tickers: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)

class IntelEventV1(BaseModel):
    type: str
    title: str = Field(..., min_length=1)
    summary: Optional[str] = None
    url: Optional[HttpUrl] = None
    ts: Optional[int] = None
    source: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    severity: Optional[Severity] = None
    classification: Optional[IntelClassificationV1] = None
    data: dict[str, Any] = Field(default_factory=dict)

class IntelBatch(BaseModel):
    items: list[IntelEventV1]
