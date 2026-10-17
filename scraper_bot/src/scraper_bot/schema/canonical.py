from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, HttpUrl

class CanonicalItem(BaseModel):
    item_type: str
    title: str = Field(..., min_length=1)
    summary: Optional[str] = None
    url: Optional[HttpUrl] = None
    published_at: Optional[datetime] = None
    scraped_at: datetime
    source: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    severity: Optional[str] = None
    classification: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
