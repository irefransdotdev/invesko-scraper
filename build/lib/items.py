from dataclasses import dataclass

SOURCE_UITF = "uitf"
SOURCE_MUTUAL = "mutual"
MUTUAL_BATCH_KEY = "pifa"


@dataclass
class NavpuItem:
    source: str
    batch_key: str
    fund_name: str
    fund_id: str | None = None
    batch_name: str | None = None
    as_of_date: str | None = None
    category: str | None = None
    classification: str | None = None
    currency: str | None = None
    navpu: float | None = None
    navpu_as_of: str | None = None
    roi_1y: float | None = None
    roi_3y: float | None = None
    roi_5y: float | None = None
    roi_10y: float | None = None
    roi_ytd: float | None = None
    details_url: str | None = None
    scraped_at: str | None = None


@dataclass
class BatchDoneItem:
    """Marker: all funds for one scrape batch have been yielded.

    The database pipeline flushes its buffer (one transaction per batch)
    when it sees this, then drops the marker so feeds never export it.

    batch_key: bank_id for source='uitf', 'pifa' for source='mutual'.
    """

    source: str
    batch_key: str
    batch_name: str | None = None
    as_of_date: str | None = None
